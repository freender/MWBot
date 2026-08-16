import importlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


class DummyTeleBot:
    def __init__(self, token):
        self.token = token
        self.sent_messages = []

    def message_handler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def callback_query_handler(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def send_message(self, *args, **kwargs):
        self.sent_messages.append((args, kwargs))
        return mock.Mock(message_id=999)

    def delete_message(self, *args, **kwargs):
        return None

    def edit_message_text(self, *args, **kwargs):
        return None

    def answer_callback_query(self, *args, **kwargs):
        return None

    def send_chat_action(self, *args, **kwargs):
        return None

    def edit_message_reply_markup(self, *args, **kwargs):
        return None

    def clear_step_handler_by_chat_id(self, *args, **kwargs):
        return None

    def register_next_step_handler_by_chat_id(self, *args, **kwargs):
        return None

    def set_my_commands(self, *args, **kwargs):
        return None

    def infinity_polling(self, *args, **kwargs):
        return None


def load_main_module(temp_dir):
    env = {
        'TOKEN': 'token',
        'CHAT_ID': '100',
        'NOTIFY_CHAT_ID': '200',
        'WAF_TOKEN': 'waf-token',
        'WAF_ZONE': 'zone',
        'WAF_RULESET': 'ruleset',
        'WAF_RULEID': 'rule',
        'CDN_URL': 'example.com',
        'MW_BOT_ASN_DEFAULT': '1234',
        'ACCESS_CHECK_API_URL': 'https://access-check.example.com',
        'ACCESS_CHECK_API_TOKEN': 'access-check-token',
        'ALERTMANAGER_URL': 'http://alertmanager.local:9093',
        'GITHUB_INCIDENT_REPO': 'freender/homelab-ops',
        'GITHUB_INCIDENT_TOKEN': 'github-token',
        'TZ': 'UTC',
        'SEERR_BASE_URL': 'https://seerr.example.com',
        'SEERR_PUBLIC_URL': 'https://seerr.example.com',
        'SEERR_API_KEY': 'seerr-key',
        'SONARR_BASE_URL': 'https://sonarr.example.com',
        'SONARR_API_KEY': 'sonarr-key',
        'RADARR_BASE_URL': 'https://radarr.example.com',
        'RADARR_API_KEY': 'radarr-key',
        'SONARR4K_BASE_URL': 'https://sonarr4k.example.com',
        'SONARR4K_API_KEY': 'sonarr4k-key',
        'RADARR4K_BASE_URL': 'https://radarr4k.example.com',
        'RADARR4K_API_KEY': 'radarr4k-key',
    }
    os.environ.update(env)
    src_path = str(Path(__file__).resolve().parents[1] / 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    for name in [
        'cfg',
        'modules',
        'modules.alertmanager',
        'modules.common',
        'modules.firewall',
        'modules.incidents',
        'modules.maintenance',
        'modules.network_check',
        'modules.redownload',
        'main',
    ]:
        sys.modules.pop(name, None)

    with mock.patch('telebot.TeleBot', DummyTeleBot):
        cfg = importlib.import_module('cfg')
        modules = importlib.import_module('modules')
        maintenance = importlib.import_module('modules.maintenance')
        setattr(maintenance, 'STATE_FILE', os.path.join(temp_dir, 'mw_state.json'))
        setattr(maintenance, 'ALERTMANAGER_STATE_FILE', os.path.join(temp_dir, 'alertmanager_mw_state.json'))
        main = importlib.import_module('main')

    return cfg, modules, main


def button_texts(markup):
    return [button.text for row in markup.keyboard for button in row]


def make_alert(alertname='SystemdUnitFailed', host='ace', severity='warning',
               description='ace has a failed systemd unit', source=None, fingerprint=None):
    labels = {'alertname': alertname, 'host': host, 'severity': severity}
    if source:
        labels['source'] = source
    alert = {
        'labels': labels,
        'annotations': {'description': description},
        'startsAt': '2026-08-09T05:06:00.000Z',
        'status': {},
    }
    if fingerprint:
        alert['fingerprint'] = fingerprint
    return alert


def make_call(user_id, data='callback', chat_id=100, message_id=55):
    return mock.Mock(
        id='call-id',
        data=data,
        from_user=mock.Mock(id=user_id),
        message=mock.Mock(chat=mock.Mock(id=chat_id), message_id=message_id),
    )


class MainAuthTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg, self.modules, self.main = load_main_module(self.temp_dir.name)
        self.main._seerr_access_cache = self.modules._seerr_access_cache
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': {10, 20},
            'owner_chat_ids': {10},
            'loaded': True,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_home_menu_hides_sections_for_unauthorized_user(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_home_menu(30, user_id=30)

        text = show_menu.call_args.args[1]
        markup = show_menu.call_args.args[2]

        self.assertIn('Paste this into Seerr', text)
        self.assertEqual(button_texts(markup), ['✖ Close'])

    def test_home_menu_shows_auth_sections_for_authorized_non_owner(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_home_menu(20, user_id=20)

        text = show_menu.call_args.args[1]
        markup = show_menu.call_args.args[2]
        labels = button_texts(markup)

        self.assertIn('Choose a section', text)
        self.assertIn('📡 Plex Access', labels)
        self.assertIn('🎬 Media', labels)
        self.assertNotIn('🔕 Alertmanager MW', labels)

    def test_home_menu_shows_all_sections_for_owner(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_home_menu(10, user_id=10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertIn('📡 Plex Access', labels)
        self.assertIn('🎬 Media', labels)
        self.assertIn('🚨 Alerts', labels)

    def test_home_menu_offers_one_alerts_section_not_two(self):
        """Maintenance and incident filing are verbs inside Alerts, not sibling menus."""
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_home_menu(10, user_id=10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertEqual(labels, ['📡 Plex Access', '🎬 Media', '🚨 Alerts', '✖ Close'])

    def test_incident_command_always_opens_the_alert_picker(self):
        """Free text and replied-to text are ignored: alerts are the only incident source."""
        message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=10),
            text='/incident plex is down',
            reply_to_message=mock.Mock(text='CRITICAL: plex is missing', caption=None),
        )

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_show_incident_picker') as show_picker:
            self.main.command_incident(message)

        create_incident.assert_not_called()
        show_picker.assert_called_once_with(100, 10)

    def test_incident_button_is_owner_only(self):
        call = make_call(20, data='incident_new')

        with mock.patch.object(self.main, '_show_incident_picker') as show_picker, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main._handle_incident_new(call)

        show_picker.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)

    def test_incident_command_is_owner_only(self):
        for user_id in (20, 30):
            message = mock.Mock(
                chat=mock.Mock(id=100),
                from_user=mock.Mock(id=user_id),
                text='/incident plex is down',
                reply_to_message=None,
            )

            with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
                 mock.patch.object(self.main, '_show_incident_picker') as show_picker, \
                 mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
                self.main.command_incident(message)

            create_incident.assert_not_called()
            show_picker.assert_not_called()
            answer_not_allowed.assert_called_once_with(100)

    def test_incident_picker_files_in_one_tap(self):
        """/incident keeps filing one tap deep; the Alerts list is the two-tap path."""
        alerts = [make_alert(), make_alert(alertname='ContainerMissing', host='tower', severity='critical')]

        with mock.patch('modules.alertmanager.get_alert_choices', return_value=alerts), \
             mock.patch.object(self.main, 'get_open_incident_index', return_value={}), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_incident_picker(100, 10)

        markup = show_menu.call_args.args[2]
        labels = button_texts(markup)

        self.assertIn('🟡 ace: SystemdUnitFailed', labels)
        self.assertIn('🔴 tower: ContainerMissing', labels)
        self.assertEqual(
            [button.callback_data for row in markup.keyboard for button in row],
            ['alert_incident:0', 'alert_incident:1', 'nav_alerts'],
        )
        self.assertEqual(self.main._pending_alerts['100:10'], alerts)

    def test_incident_picker_offers_no_free_text_escape_hatch(self):
        alerts = [make_alert()]

        with mock.patch('modules.alertmanager.get_alert_choices', return_value=alerts), \
             mock.patch.object(self.main, 'get_open_incident_index', return_value={}), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_incident_picker(100, 10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertEqual(labels, ['🟡 ace: SystemdUnitFailed', '⬅ Alerts'])

    def test_incident_picker_stops_when_alertmanager_unavailable(self):
        with mock.patch('modules.alertmanager.get_alert_choices', return_value=None), \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main._show_incident_picker(100, 10)

        self.assertIn('Alertmanager is unavailable', show_result.call_args.args[1])
        self.assertNotIn('100:10', self.main._pending_alerts)

    def test_incident_picker_stops_when_nothing_is_firing(self):
        with mock.patch('modules.alertmanager.get_alert_choices', return_value=[]), \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main._show_incident_picker(100, 10)

        self.assertIn('Nothing is firing', show_result.call_args.args[1])

    def test_alert_incident_creates_incident_from_alert(self):
        self.main._pending_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='alert_incident:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident:
            self.main.handle_callback(call)

        summary = create_incident.call_args.args[1]

        self.assertTrue(summary.startswith('SystemdUnitFailed on ace'))
        self.assertIn('ace has a failed systemd unit', summary)
        self.assertIn('- severity: warning', summary)

    def test_alert_incident_is_owner_only(self):
        self.main._pending_alerts['100:20'] = [make_alert()]
        call = make_call(20, data='alert_incident:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main.handle_callback(call)

        create_incident.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)

    def test_alert_incident_reports_expired_list(self):
        call = make_call(10, data='alert_incident:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main.handle_callback(call)

        create_incident.assert_not_called()
        self.assertIn('expired', show_result.call_args.args[1])

    def test_announce_triage_report_targets_the_owner_chat_only(self):
        # owner_chat_ids is {10} from setUp; cfg.CHAT_ID ('100') is the shared alert
        # broadcast chat and must never see a prompt to go and authorise a deploy.
        self.main._announce_triage_report({
            'issue': 15,
            'url': 'https://github.com/freender/homelab-ops/issues/15',
        })

        self.assertEqual(len(self.main.bot.sent_messages), 1)
        args, kwargs = self.main.bot.sent_messages[0]
        chat_id = args[0] if args else kwargs.get('chat_id')
        self.assertEqual(chat_id, 10)
        self.assertNotEqual(str(chat_id), self.cfg.CHAT_ID)
        text = args[1] if len(args) > 1 else kwargs.get('text')
        self.assertIn('Triage complete', text)
        self.assertIn('/fix', text)

    def test_announce_triage_report_carries_no_action_button(self):
        # A button that fixed the incident from here would move the authority to deploy off
        # GitHub and into this chat, where the owner token is what would be acting.
        self.main._announce_triage_report({
            'issue': 15,
            'url': 'https://github.com/freender/homelab-ops/issues/15',
        })

        _, kwargs = self.main.bot.sent_messages[0]
        markup = kwargs.get('reply_markup')
        buttons = [button for row in (markup.keyboard if markup else []) for button in row]
        self.assertTrue(all(getattr(button, 'url', None) for button in buttons))

    def test_announce_triage_report_reaches_every_owner_chat(self):
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': {10, 11, 20},
            'owner_chat_ids': {10, 11},
            'loaded': True,
        })

        self.main._announce_triage_report({'issue': 15, 'url': None})

        chat_ids = sorted(
            (args[0] if args else kwargs.get('chat_id'))
            for args, kwargs in self.main.bot.sent_messages
        )
        self.assertEqual(chat_ids, [10, 11])

    def test_announce_triage_report_logs_and_sends_nothing_without_an_owner_chat(self):
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': set(),
            'owner_chat_ids': set(),
            'loaded': True,
        })

        with self.assertLogs(level='ERROR') as logs:
            self.main._announce_triage_report({'issue': 15, 'url': None})

        self.assertEqual(self.main.bot.sent_messages, [])
        self.assertTrue(any('No owner chat id' in message for message in logs.output))

    def test_home_menu_tracks_latest_message_id(self):
        with mock.patch.object(self.main, '_show_menu', return_value=77) as show_menu:
            self.main._show_home_menu(10, user_id=10)

        show_menu.assert_called_once()
        self.assertEqual(self.main._home_menu_messages[10], 77)

    def test_show_menu_ignores_not_modified_error(self):
        markup = mock.Mock()

        with mock.patch.object(self.main.bot, 'edit_message_text', side_effect=Exception('Bad Request: message is not modified')) as edit_message_text, \
             mock.patch.object(self.main.bot, 'send_message') as send_message:
            message_id = self.main._show_menu(100, 'same text', markup, message_id=55)

        edit_message_text.assert_called_once()
        send_message.assert_not_called()
        self.assertEqual(message_id, 55)

    def test_show_menu_sends_new_message_after_edit_failure(self):
        markup = mock.Mock()

        with mock.patch.object(self.main.bot, 'edit_message_text', side_effect=Exception('boom')) as edit_message_text, \
             mock.patch.object(self.main.bot, 'send_message', return_value=mock.Mock(message_id=88)) as send_message:
            message_id = self.main._show_menu(100, 'new text', markup, message_id=55)

        edit_message_text.assert_called_once()
        send_message.assert_called_once()
        self.assertEqual(message_id, 88)

    def test_start_replaces_previous_home_menu_message(self):
        message = mock.Mock(chat=mock.Mock(id=100), from_user=mock.Mock(id=20))
        self.main._home_menu_messages[100] = 44

        with mock.patch.object(self.main, '_delete_bot_message', return_value=True) as delete_bot_message, \
             mock.patch.object(self.main, '_show_home_menu') as show_home_menu:
            self.main.command_start(message)

        delete_bot_message.assert_called_once_with(100, 44)
        show_home_menu.assert_called_once_with(100, user_id=20)
        self.assertNotIn(100, self.main._home_menu_messages)

    def test_plex_menu_hides_reset_for_authorized_non_owner(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_plex_menu(20, user_id=20)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertEqual(labels, ['✅ Allow Plex', '📋 Status', '⬅ Back'])

    def test_plex_menu_shows_reset_for_owner(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_plex_menu(10, user_id=10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertEqual(labels, ['✅ Allow Plex', '🧹 Remove Access', '📋 Status', '⬅ Back'])

    def test_start_network_check_shows_worker_link_and_tracks_session(self):
        session = {'id': 'session-id', 'check_url': 'https://access-check.example.com/check/session-id'}
        poll_thread = mock.Mock()
        with mock.patch.object(self.main, 'create_network_check', return_value=(session, None)), \
             mock.patch.object(self.main, '_show_menu', return_value=55) as show_menu, \
             mock.patch.object(self.main.threading, 'Thread', return_value=poll_thread):
            self.main._start_network_check(100, 20, message_id=55)

        pending = self.main._pending_network_checks['100:20']
        self.assertEqual(pending['id'], 'session-id')
        self.assertEqual(pending['message_id'], 55)
        markup = show_menu.call_args.args[2]
        self.assertEqual(
            button_texts(markup),
            ['🌐 Detect Current Network', '⬅ Back'],
        )
        poll_thread.start.assert_called_once_with()

    def test_poll_detected_network_grants_and_consumes_session(self):
        pending = {
            'id': 'session-id',
            'check_url': 'https://access-check.example.com/check/session-id',
            'chat_id': 100,
            'user_id': 20,
            'message_id': 55,
        }
        self.main._pending_network_checks['100:20'] = pending
        detected = {'status': 'complete', 'asn': '7922', 'as_organization': 'Comcast Cable'}

        with mock.patch.object(self.main, 'get_network_check', return_value=(detected, None)), \
             mock.patch.object(self.main, 'grant_network_access', return_value=(True, 'access granted')) as grant_access, \
             mock.patch.object(self.main, 'delete_network_check', return_value=True) as delete_check, \
             mock.patch.object(self.main, '_show_plex_result') as show_result, \
             mock.patch.object(self.main.shutdown_event, 'wait', return_value=False), \
             mock.patch.object(self.main.time, 'monotonic', return_value=0):
            self.main._poll_network_check('100:20', pending)

        grant_access.assert_called_once_with('7922', 'Comcast Cable')
        delete_check.assert_called_once_with('session-id')
        show_result.assert_called_once_with(
            100,
            '✅ Access Enabled\nYour current network now has Plex access.',
            user_id=20,
            message_id=55,
        )
        self.assertNotIn('100:20', self.main._pending_network_checks)

    def test_poll_detected_network_waits_until_complete(self):
        pending = {
            'id': 'session-id',
            'check_url': 'https://access-check.example.com/check/session-id',
            'chat_id': 100,
            'user_id': 20,
            'message_id': 55,
        }
        self.main._pending_network_checks['100:20'] = pending
        complete = {'status': 'complete', 'asn': '7922'}

        with mock.patch.object(
            self.main,
            'get_network_check',
            side_effect=[({'status': 'pending'}, None), (complete, None)],
        ) as get_check, mock.patch.object(
            self.main,
            'grant_network_access',
            return_value=(True, 'access granted'),
        ), mock.patch.object(
            self.main,
            'delete_network_check',
            return_value=True,
        ), mock.patch.object(
            self.main,
            '_show_plex_result',
        ), mock.patch.object(
            self.main.shutdown_event,
            'wait',
            return_value=False,
        ), mock.patch.object(self.main.time, 'monotonic', return_value=0):
            self.main._poll_network_check('100:20', pending)

        self.assertEqual(get_check.call_count, 2)

    def test_poll_does_not_grant_replaced_session(self):
        pending = {
            'id': 'old-session',
            'check_url': 'https://access-check.example.com/check/old-session',
            'chat_id': 100,
            'user_id': 20,
            'message_id': 55,
        }
        replacement = {**pending, 'id': 'new-session'}
        self.main._pending_network_checks['100:20'] = pending
        complete = {'status': 'complete', 'asn': '7922'}

        def replace_while_reading(_session_id):
            self.main._pending_network_checks['100:20'] = replacement
            return complete, None

        with mock.patch.object(self.main, 'get_network_check', side_effect=replace_while_reading), \
             mock.patch.object(self.main, 'grant_network_access') as grant_access, \
             mock.patch.object(self.main.shutdown_event, 'wait', return_value=False), \
             mock.patch.object(self.main.time, 'monotonic', return_value=0):
            self.main._poll_network_check('100:20', pending)

        grant_access.assert_not_called()
        self.assertIs(self.main._pending_network_checks['100:20'], replacement)

    def test_poll_does_not_grant_network_access_after_authorization_is_revoked(self):
        pending = {
            'id': 'session-id',
            'check_url': 'https://access-check.example.com/check/session-id',
            'chat_id': 100,
            'user_id': 20,
            'message_id': 55,
        }
        self.main._pending_network_checks['100:20'] = pending
        self.modules._seerr_access_cache['authorized_chat_ids'].remove(20)

        with mock.patch.object(
            self.main,
            'get_network_check',
            return_value=({'status': 'complete', 'asn': '7922'}, None),
        ), mock.patch.object(self.main, 'grant_network_access') as grant_access, \
             mock.patch.object(self.main, 'delete_network_check', return_value=True) as delete_check, \
             mock.patch.object(self.main, '_show_plex_result') as show_result, \
             mock.patch.object(self.main.shutdown_event, 'wait', return_value=False), \
             mock.patch.object(self.main.time, 'monotonic', return_value=0):
            self.main._poll_network_check('100:20', pending)

        grant_access.assert_not_called()
        delete_check.assert_called_once_with('session-id')
        show_result.assert_called_once_with(
            100,
            '❌ Access Not Enabled\nAccess permission is no longer available. Select Allow Plex and try again.',
            user_id=20,
            message_id=55,
        )
        self.assertNotIn('100:20', self.main._pending_network_checks)

    def test_finish_network_check_shows_explicit_failure_in_same_menu(self):
        pending = {
            'id': 'session-id',
            'check_url': 'https://access-check.example.com/check/session-id',
            'chat_id': 100,
            'user_id': 20,
            'message_id': 55,
        }
        self.main._pending_network_checks['100:20'] = pending

        with mock.patch.object(self.main, '_show_plex_result') as show_result:
            self.main._finish_network_check('100:20', pending, 'Cloudflare update failed.', success=False)

        show_result.assert_called_once_with(
            100,
            '❌ Access Not Enabled\nCloudflare update failed.',
            user_id=20,
            message_id=55,
        )

    def test_legacy_apply_button_explains_automatic_detection(self):
        call = make_call(20, data='plex_detect_apply')
        with mock.patch.object(self.main, '_show_plex_result') as show_result:
            self.main._handle_plex_detect_apply(call)

        self.assertIn('automatic', show_result.call_args.args[1])

    def test_nav_plex_rejects_unauthorized_user(self):
        call = make_call(30, data='nav_plex')

        with mock.patch.object(self.main, '_show_plex_menu') as show_plex_menu, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_plex(call)

        show_plex_menu.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_nav_plex_allows_authorized_user(self):
        call = make_call(20, data='nav_plex')

        with mock.patch.object(self.main, '_show_plex_menu') as show_plex_menu, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_plex(call)

        show_plex_menu.assert_called_once_with(100, user_id=20, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

    def test_nav_media_rejects_unauthorized_user(self):
        call = make_call(30, data='nav_media')

        with mock.patch.object(self.main, '_show_media_menu') as show_media_menu, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_media(call)

        show_media_menu.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_nav_media_allows_authorized_user(self):
        call = make_call(20, data='nav_media')

        with mock.patch.object(self.main, '_show_media_menu') as show_media_menu, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_media(call)

        show_media_menu.assert_called_once_with(100, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

    def test_nav_alerts_rejects_authorized_non_owner(self):
        call = make_call(20, data='nav_alerts')

        with mock.patch.object(self.main, '_show_alerts_menu') as show_menu, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main._handle_nav_alerts(call)

        show_menu.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)

    def test_nav_alerts_allows_owner(self):
        call = make_call(10, data='nav_alerts')

        with mock.patch.object(self.main, '_show_alerts_menu') as show_menu:
            self.main._handle_nav_alerts(call)

        show_menu.assert_called_once_with(100, 10, message_id=55)

    def test_retired_home_buttons_still_reach_the_alerts_section(self):
        """Old menu messages stay in the chat forever; their buttons must not go dead."""
        call = make_call(10, data='nav_am_mw')

        with mock.patch.object(self.main, '_show_alerts_menu') as show_menu:
            self.main.handle_callback(call)

        show_menu.assert_called_once_with(100, 10, message_id=55)

    def test_plex_reset_rejects_authorized_non_owner(self):
        call = make_call(20, data='plex_reset')

        with mock.patch.object(self.main, 'disable_asn_to_firewall_rule') as disable_rule, \
             mock.patch.object(self.main, '_show_plex_result') as show_plex_result, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_plex_reset(call)

        disable_rule.assert_not_called()
        show_plex_result.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_plex_reset_allows_owner(self):
        call = make_call(10, data='plex_reset')

        with mock.patch.object(self.main, 'disable_asn_to_firewall_rule', return_value='done') as disable_rule, \
             mock.patch.object(self.main, '_show_plex_result') as show_plex_result, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query, \
             mock.patch.object(self.main.bot, 'send_chat_action') as send_chat_action:
            self.main._handle_plex_reset(call)

        disable_rule.assert_called_once_with()
        send_chat_action.assert_called_once_with(100, 'typing')
        show_plex_result.assert_called_once_with(100, 'done', user_id=10, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

    def test_plex_status_rejects_unauthorized_user(self):
        call = make_call(30, data='plex_status')

        with mock.patch.object(self.main, 'get_firewall_status_text') as get_status, \
             mock.patch.object(self.main, '_show_plex_result') as show_plex_result, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_plex_status(call)

        get_status.assert_not_called()
        show_plex_result.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_plex_status_allows_authorized_user(self):
        call = make_call(20, data='plex_status')

        with mock.patch.object(self.main, 'get_firewall_status_text', return_value='status text') as get_status, \
             mock.patch.object(self.main, '_show_plex_result') as show_plex_result, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query, \
             mock.patch.object(self.main.bot, 'send_chat_action') as send_chat_action:
            self.main._handle_plex_status(call)

        get_status.assert_called_once_with()
        send_chat_action.assert_called_once_with(100, 'typing')
        show_plex_result.assert_called_once_with(100, 'status text', user_id=20, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

    def test_media_redownload_rejects_unauthorized_user(self):
        call = make_call(30, data='media_redownload')

        with mock.patch.object(self.main, '_start_redownload_flow') as start_redownload_flow, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_media_redownload(call)

        start_redownload_flow.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_media_redownload_allows_authorized_user(self):
        call = make_call(20, data='media_redownload')

        with mock.patch.object(self.main, '_start_redownload_flow') as start_redownload_flow, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_media_redownload(call)

        start_redownload_flow.assert_called_once_with(100, 20, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

    def test_menu_close_deletes_message(self):
        call = make_call(20, data='menu_close')
        self.main._home_menu_messages[100] = 55

        with mock.patch.object(self.main, '_delete_bot_message', return_value=True) as delete_bot_message, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query, \
             mock.patch.object(self.main.bot, 'edit_message_reply_markup') as edit_message_reply_markup:
            self.main._handle_menu_close(call)

        delete_bot_message.assert_called_once_with(100, 55)
        edit_message_reply_markup.assert_not_called()
        answer_callback_query.assert_called_once_with('call-id')
        self.assertNotIn(100, self.main._home_menu_messages)

    def test_menu_close_falls_back_to_clearing_buttons(self):
        call = make_call(20, data='menu_close')
        self.main._home_menu_messages[100] = 55

        with mock.patch.object(self.main, '_delete_bot_message', return_value=False) as delete_bot_message, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query, \
             mock.patch.object(self.main.bot, 'edit_message_reply_markup') as edit_message_reply_markup:
            self.main._handle_menu_close(call)

        delete_bot_message.assert_called_once_with(100, 55)
        edit_message_reply_markup.assert_called_once_with(chat_id=100, message_id=55, reply_markup=None)
        answer_callback_query.assert_called_once_with('call-id')
        self.assertNotIn(100, self.main._home_menu_messages)

    def test_redownload_issue_callback_rejects_unauthorized_user(self):
        call = make_call(30, data='redownload_issue:29')

        with mock.patch.object(self.main, 'resolve_redownload_issue') as resolve_redownload_issue, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main.handle_callback(call)

        resolve_redownload_issue.assert_not_called()
        answer_callback_query.assert_called_once_with('call-id', text='Not authorized')

    def test_redownload_confirmation_does_not_execute_after_authorization_is_revoked(self):
        call = make_call(20, data='redownload_confirm')
        self.main._pending_redownloads['100:20'] = {'media_type': 'movie'}
        self.modules._seerr_access_cache['authorized_chat_ids'].remove(20)

        with mock.patch.object(self.main, 'execute_redownload') as execute_redownload, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main.handle_callback(call)

        execute_redownload.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        self.assertNotIn('100:20', self.main._pending_redownloads)

    def test_owner_only_alertmanager_callbacks_reject_authorized_non_owner(self):
        call = make_call(20)
        handlers = [
            self.main._handle_alertmanager_mw_start,
            self.main._handle_alertmanager_mw_stop,
        ]

        with mock.patch.object(self.main, 'start_alertmanager_mw') as start_mw, \
             mock.patch.object(self.main, 'stop_alertmanager_mw') as stop_mw, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            for handler in handlers:
                handler(call)

        start_mw.assert_not_called()
        stop_mw.assert_not_called()
        show_alerts.assert_not_called()

    def test_owner_only_alert_action_callbacks_reject_authorized_non_owner(self):
        self.main._pending_alerts['100:20'] = [make_alert(source='pve')]
        call = make_call(20)
        handlers = [
            self.main._handle_alert_pick,
            self.main._handle_alert_silence,
            self.main._handle_alert_unsilence,
            self.main._handle_alert_resolve,
            self.main._handle_alert_resolve_confirm,
        ]

        with mock.patch('modules.alertmanager.silence_alert') as silence, \
             mock.patch('modules.alertmanager.unsilence_alert') as unsilence, \
             mock.patch('modules.alertmanager.resolve_alert') as resolve, \
             mock.patch.object(self.main, '_show_menu') as show_menu, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            for handler in handlers:
                handler(call, '0')

        silence.assert_not_called()
        unsilence.assert_not_called()
        resolve.assert_not_called()
        show_menu.assert_not_called()
        show_alerts.assert_not_called()
        self.assertEqual(answer_not_allowed.call_count, len(handlers))

    def test_alertmanager_start_callback_returns_to_the_alert_list(self):
        call = make_call(10, data='am_mw_start')

        with mock.patch.object(self.main, 'start_alertmanager_mw', return_value='started') as start_mw, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main._handle_alertmanager_mw_start(call)

        start_mw.assert_called_once_with()
        show_alerts.assert_called_once_with(100, 10, message_id=55, notice='started')


class OwnerOnlySurfaceTest(unittest.TestCase):
    """Nothing monitoring-related may run for anyone but the owner.

    Driven off the dispatch maps rather than a hand-written list, so a new alert action
    is covered the moment it is registered instead of when someone remembers to test it.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg, self.modules, self.main = load_main_module(self.temp_dir.name)
        self.main._seerr_access_cache = self.modules._seerr_access_cache
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': {10, 20},
            'owner_chat_ids': {10},
            'loaded': True,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def monitoring_callbacks(self):
        alert_actions = [prefix + '0' for prefix in self.main.ALERT_CALLBACK_HANDLERS]
        return sorted(self.main.OWNER_ONLY_CALLBACKS) + sorted(alert_actions)

    def test_every_alert_action_is_owner_only_by_registration(self):
        for prefix in self.main.ALERT_CALLBACK_HANDLERS:
            self.assertTrue(
                self.main._is_owner_only_callback(prefix + '0'),
                f'{prefix} is dispatchable but not owner-gated',
            )

    def monitoring_boundary(self):
        """Patch where monitoring leaves the process, and only the noise around it.

        Nothing on the monitoring path is stubbed -- not `_show_alerts_menu`, not
        `start_alertmanager_mw`, not incident filing -- so a handler that skips its gate
        really does run and really does get caught here. The Plex and redownload flows are
        stubbed because they are not monitoring and would otherwise spawn a poll thread.
        """
        alertmanager = importlib.import_module('modules.alertmanager')
        incidents = importlib.import_module('modules.incidents')
        firewall = importlib.import_module('modules.firewall')
        return {
            'alertmanager': mock.patch.object(alertmanager, 'request_json'),
            'github_get': mock.patch.object(incidents.requests, 'get'),
            'github_post': mock.patch.object(incidents.requests, 'post'),
            'noise': [
                mock.patch.object(firewall, 'request_json'),
                mock.patch.object(self.main, '_start_network_check'),
                mock.patch.object(self.main, '_start_redownload_flow'),
                mock.patch.object(self.main, '_show_menu'),
            ],
        }

    def assert_no_monitoring_reached(self, call):
        boundary = self.monitoring_boundary()
        with boundary['alertmanager'] as alertmanager_request, \
                boundary['github_get'] as github_get, \
                boundary['github_post'] as github_post:
            for patcher in boundary['noise']:
                patcher.start()
            try:
                self.main.handle_callback(call)
            finally:
                for patcher in boundary['noise']:
                    patcher.stop()

        alertmanager_request.assert_not_called()
        github_get.assert_not_called()
        github_post.assert_not_called()

    def test_no_monitoring_callback_reaches_alertmanager_or_github_for_a_non_owner(self):
        # 20 is authorized for Plex/Media but not the owner; 30 is a stranger.
        for user_id in (20, 30):
            for data in self.monitoring_callbacks():
                with self.subTest(user_id=user_id, callback=data):
                    self.main._pending_alerts[f'100:{user_id}'] = [make_alert(source='pve')]
                    self.assert_no_monitoring_reached(make_call(user_id, data=data))

    def test_no_callback_at_all_reaches_monitoring_for_a_non_owner(self):
        """Behavioural, not declarative, and over every registered callback.

        The declared owner-only set could itself be wrong: a monitoring callback added
        with no gate anywhere would agree with its own (missing) declaration. This asks
        the only question that matters instead -- can a non-owner make *any* callback
        reach Alertmanager or the incident repo.
        """
        for user_id in (20, 30):
            for data in list(self.main.CALLBACK_HANDLERS):
                with self.subTest(user_id=user_id, callback=data):
                    self.main._pending_alerts[f'100:{user_id}'] = [make_alert(source='pve')]
                    self.assert_no_monitoring_reached(make_call(user_id, data=data))

    def test_declared_owner_only_matches_what_the_handlers_actually_refuse(self):
        """The central gate and the per-handler checks must not drift apart.

        A callback an authorized non-owner is refused is exactly a callback declared
        owner-only: a handler that grows its own check without being registered, or is
        registered without checking, fails here.
        """
        for data, handler in self.main.CALLBACK_HANDLERS.items():
            with self.subTest(callback=data):
                call = make_call(20, data=data)
                with mock.patch.object(self.main, '_answer_not_allowed') as not_allowed, \
                     mock.patch.object(self.main, '_show_alerts_menu'), \
                     mock.patch.object(self.main, '_show_incident_picker'), \
                     mock.patch.object(self.main, '_show_plex_menu'), \
                     mock.patch.object(self.main, '_show_media_menu'), \
                     mock.patch.object(self.main, '_show_plex_result'), \
                     mock.patch.object(self.main, '_start_network_check'), \
                     mock.patch.object(self.main, '_start_redownload_flow'), \
                     mock.patch.object(self.main, 'start_alertmanager_mw'), \
                     mock.patch.object(self.main, 'stop_alertmanager_mw'), \
                     mock.patch.object(self.main, 'disable_asn_to_firewall_rule'), \
                     mock.patch.object(self.main, 'get_firewall_status_text'), \
                     mock.patch.object(self.main, '_show_home_menu'):
                    handler(call)

                self.assertEqual(
                    not_allowed.called,
                    self.main._is_owner_only_callback(data),
                    f'{data}: refusal and owner-only declaration disagree',
                )

    def test_owner_reaches_the_alerts_section(self):
        """The gate must not be so tight it locks the owner out too."""
        call = make_call(10, data='nav_alerts')

        with mock.patch.object(self.main, '_show_alerts_menu') as show_alerts, \
             mock.patch.object(self.main, '_answer_not_allowed') as not_allowed:
            self.main.handle_callback(call)

        show_alerts.assert_called_once_with(100, 10, message_id=55)
        not_allowed.assert_not_called()

    def test_losing_owner_identity_closes_the_section(self):
        """Seerr unreachable empties the owner set, and that must fail closed."""
        self.modules._seerr_access_cache.update({'owner_chat_ids': set()})
        alertmanager = importlib.import_module('modules.alertmanager')

        for data in self.monitoring_callbacks():
            with self.subTest(callback=data):
                call = make_call(10, data=data)
                with mock.patch.object(alertmanager, 'request_json') as request_json, \
                     mock.patch.object(self.main, '_show_menu') as show_menu:
                    self.main.handle_callback(call)

                request_json.assert_not_called()
                show_menu.assert_not_called()

    def test_incident_command_is_refused_without_owner_identity(self):
        self.modules._seerr_access_cache.update({'owner_chat_ids': set()})
        message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=10),
            text='/incident',
            reply_to_message=None,
        )

        with mock.patch.object(self.main, '_show_incident_picker') as show_picker, \
             mock.patch.object(self.main, '_answer_not_allowed') as not_allowed:
            self.main.command_incident(message)

        show_picker.assert_not_called()
        not_allowed.assert_called_once_with(100)

    def test_a_message_without_a_sender_is_not_the_owner(self):
        """`from_user` is absent on channel posts; None must not match an owner id."""
        message = mock.Mock(chat=mock.Mock(id=100), from_user=None, text='/incident')

        with mock.patch.object(self.main, '_show_incident_picker') as show_picker, \
             mock.patch.object(self.main, '_answer_not_allowed') as not_allowed:
            self.main.command_incident(message)

        show_picker.assert_not_called()
        not_allowed.assert_called_once_with(100)

    def test_home_menu_hides_the_alerts_section_from_a_non_owner(self):
        for user_id in (20, 30):
            with self.subTest(user_id=user_id):
                with mock.patch.object(self.main, '_show_menu') as show_menu:
                    self.main._show_home_menu(100, user_id=user_id)

                self.assertNotIn('🚨 Alerts', button_texts(show_menu.call_args.args[2]))


class AlertsMenuTest(unittest.TestCase):
    """The alerts list is the section: status, filing, resolving and silencing in one view."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg, self.modules, self.main = load_main_module(self.temp_dir.name)
        self.main._seerr_access_cache = self.modules._seerr_access_cache
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': {10, 20},
            'owner_chat_ids': {10},
            'loaded': True,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def _show_alerts(self, alerts, incidents=None, window=''):
        with mock.patch('modules.alertmanager.get_alert_choices', return_value=alerts), \
             mock.patch.object(self.main, 'get_open_incident_index', return_value=incidents or {}), \
             mock.patch.object(self.main, 'get_alertmanager_window_text', return_value=window), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_alerts_menu(100, 10)
        return show_menu.call_args.args[1], show_menu.call_args.args[2]

    def test_alert_list_is_the_menu(self):
        alerts = [make_alert(), make_alert(alertname='ContainerMissing', host='tower', severity='critical')]

        text, markup = self._show_alerts(alerts)
        labels = button_texts(markup)

        self.assertIn('2 active alerts', text)
        self.assertIn('🟡 ace: SystemdUnitFailed', labels)
        self.assertIn('🔴 tower: ContainerMissing', labels)
        self.assertEqual(self.main._pending_alerts['100:10'], alerts)

    def test_alert_list_marks_alerts_that_are_already_filed(self):
        """The whole point of C: know it is known before tapping File Incident."""
        alert = make_alert(fingerprint='abc123def456')
        incidents = {'abc123def456': {'number': 42, 'title': 'x', 'url': 'https://example.invalid/42'}}

        _, markup = self._show_alerts([alert], incidents=incidents)

        self.assertIn('🟡 ace: SystemdUnitFailed → #42', button_texts(markup))

    def test_alert_list_survives_github_being_unreachable(self):
        alert = make_alert(fingerprint='abc123def456')

        with mock.patch('modules.alertmanager.get_alert_choices', return_value=[alert]), \
             mock.patch.object(self.main, 'get_open_incident_index', side_effect=RuntimeError('boom')), \
             mock.patch.object(self.main, 'get_alertmanager_window_text', return_value=''), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_alerts_menu(100, 10)

        self.assertIn('🟡 ace: SystemdUnitFailed', button_texts(show_menu.call_args.args[2]))

    def test_alert_list_offers_start_maintenance_when_no_window_is_active(self):
        _, markup = self._show_alerts([make_alert()], window='')

        labels = button_texts(markup)
        self.assertIn('🔕 Maintenance 12h', labels)
        self.assertNotIn('⏹ End Maintenance', labels)

    def test_alert_list_offers_end_maintenance_only_while_one_is_active(self):
        text, markup = self._show_alerts([make_alert()], window='🔕 Maintenance active — 4h left')

        labels = button_texts(markup)
        self.assertIn('⏹ End Maintenance', labels)
        self.assertNotIn('🔕 Maintenance 12h', labels)
        self.assertIn('Maintenance active', text)

    def test_alert_list_reports_an_unreachable_alertmanager(self):
        text, markup = self._show_alerts(None)

        self.assertIn('Alertmanager is unavailable', text)
        self.assertEqual(button_texts(markup), ['🔕 Maintenance 12h', '🔄 Refresh', '🏠 Home'])

    def _show_actions(self, alert, incidents=None, silenced_for=None):
        self.main._pending_alerts['100:10'] = [alert]
        ends_at = None if silenced_for is None else datetime.now(timezone.utc) + silenced_for
        with mock.patch.object(self.main, 'get_open_incident_index', return_value=incidents or {}), \
             mock.patch('modules.alertmanager.alert_silenced_until', return_value=ends_at), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_alert_actions(100, 10, '0')
        return show_menu.call_args.args[1], show_menu.call_args.args[2]

    def test_action_sheet_offers_file_and_silence_for_a_metric_alert(self):
        text, markup = self._show_actions(make_alert())

        labels = button_texts(markup)
        self.assertIn('SystemdUnitFailed on ace', text)
        self.assertIn('🚨 File Incident', labels)
        self.assertIn('🔕 Silence', labels)
        # A metric alert would be re-sent by vmalert, so resolving it is not offered.
        self.assertNotIn('✅ Resolve', labels)

    def test_action_sheet_offers_resolve_only_for_one_shot_events(self):
        _, markup = self._show_actions(make_alert(source='pve'))

        self.assertIn('✅ Resolve', button_texts(markup))

    def test_action_sheet_offers_unsilence_and_shows_time_left(self):
        text, markup = self._show_actions(make_alert(), silenced_for=timedelta(days=6, hours=3))

        labels = button_texts(markup)
        self.assertIn('🔔 Unsilence', labels)
        self.assertNotIn('🔕 Silence', labels)
        self.assertIn('6d left', text)

    def test_action_sheet_links_the_existing_incident_instead_of_filing_again(self):
        alert = make_alert(fingerprint='abc123def456')
        incidents = {'abc123def456': {'number': 42, 'title': 'x', 'url': 'https://example.invalid/42'}}

        text, markup = self._show_actions(alert, incidents=incidents)

        labels = button_texts(markup)
        self.assertIn('Already filed as #42', text)
        self.assertIn('🔗 Open Incident #42', labels)
        self.assertNotIn('🚨 File Incident', labels)

    def test_action_sheet_redraws_the_list_when_the_stash_expired(self):
        with mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main._show_alert_actions(100, 10, '0')

        self.assertIn('expired', show_alerts.call_args.kwargs['notice'])

    def test_silence_asks_for_a_duration_before_silencing(self):
        self.main._pending_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='alert_silence:0')

        with mock.patch('modules.alertmanager.silence_alert') as silence, \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main.handle_callback(call)

        silence.assert_not_called()
        markup = show_menu.call_args.args[2]
        self.assertEqual(button_texts(markup), ['1d', '3d', '7d', '⬅ Back'])
        self.assertEqual(
            [b.callback_data for row in markup.keyboard for b in row],
            ['alert_silence_do:0:0', 'alert_silence_do:0:1', 'alert_silence_do:0:2',
             'alert_pick:0'],
        )

    def test_silence_floor_is_a_full_day(self):
        """A sub-day silence does not survive a nightly re-alert; it just defers it."""
        self.assertTrue(all(d >= timedelta(days=1)
                            for d in self.cfg.ALERTMANAGER_ALERT_SILENCE_DURATIONS))

    def test_silencing_one_alert_matches_that_alert_only(self):
        alert = make_alert()
        self.main._pending_alerts['100:10'] = [alert]
        call = make_call(10, data='alert_silence_do:0:2')

        with mock.patch('modules.alertmanager.silence_alert', return_value='sil-1') as silence, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main.handle_callback(call)

        silence.assert_called_once_with(alert, timedelta(days=7))
        self.assertIn('Silenced for 7d', show_alerts.call_args.kwargs['notice'])

    def test_failed_silence_is_not_reported_as_success(self):
        self.main._pending_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='alert_silence_do:0:0')

        with mock.patch('modules.alertmanager.silence_alert', return_value=None), \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main.handle_callback(call)

        self.assertIn('Unable to silence', show_alerts.call_args.kwargs['notice'])

    def test_an_unknown_duration_choice_silences_nothing(self):
        self.main._pending_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='alert_silence_do:0:99')

        with mock.patch('modules.alertmanager.silence_alert') as silence, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main.handle_callback(call)

        silence.assert_not_called()
        self.assertIn('no longer offered', show_alerts.call_args.kwargs['notice'])

    def test_silenced_alert_row_shows_when_it_lapses(self):
        """A week-long silence is the one most likely to be forgotten."""
        alert = make_alert()
        alert['status'] = {'silencedBy': ['sil-1']}
        ends_at = datetime.now(timezone.utc) + timedelta(days=6, hours=3)

        with mock.patch('modules.alertmanager.get_alert_choices', return_value=[alert]), \
             mock.patch('modules.alertmanager.silence_index', return_value={'sil-1': {}}), \
             mock.patch('modules.alertmanager.alert_silenced_until', return_value=ends_at), \
             mock.patch.object(self.main, 'get_open_incident_index', return_value={}), \
             mock.patch.object(self.main, 'get_alertmanager_window_text', return_value=''), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_alerts_menu(100, 10)

        self.assertIn('🔇 6d', ' '.join(button_texts(show_menu.call_args.args[2])))

    def test_unsilenced_list_costs_no_silence_lookup(self):
        """Nothing suppressed means the bulk silence call is not made at all."""
        with mock.patch('modules.alertmanager.get_alert_choices', return_value=[make_alert()]), \
             mock.patch('modules.alertmanager.silence_index') as silence_index, \
             mock.patch.object(self.main, 'get_open_incident_index', return_value={}), \
             mock.patch.object(self.main, 'get_alertmanager_window_text', return_value=''), \
             mock.patch.object(self.main, '_show_menu'):
            self.main._show_alerts_menu(100, 10)

        silence_index.assert_not_called()

    def test_unsilencing_reports_when_nothing_of_ours_was_silencing(self):
        self.main._pending_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='alert_unsilence:0')

        with mock.patch('modules.alertmanager.unsilence_alert', return_value=False), \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main.handle_callback(call)

        self.assertIn('No silence of ours', show_alerts.call_args.kwargs['notice'])

    def test_resolve_confirms_before_clearing(self):
        self.main._pending_alerts['100:10'] = [make_alert(source='pve')]
        call = make_call(10, data='alert_resolve:0')

        with mock.patch('modules.alertmanager.resolve_alert') as resolve, \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main.handle_callback(call)

        resolve.assert_not_called()
        markup = show_menu.call_args.args[2]
        self.assertEqual(
            [button.callback_data for row in markup.keyboard for button in row],
            ['alert_resolve_do:0', 'alert_pick:0'],
        )

    def test_resolve_confirmation_clears_the_alert_and_returns_to_the_list(self):
        alert = make_alert(source='pve')
        self.main._pending_alerts['100:10'] = [alert]
        call = make_call(10, data='alert_resolve_do:0')

        with mock.patch('modules.alertmanager.resolve_alert', return_value=True) as resolve, \
             mock.patch.object(self.main, '_show_alerts_menu') as show_alerts:
            self.main.handle_callback(call)

        resolve.assert_called_once_with(alert)
        self.assertIn('Resolved:', show_alerts.call_args.kwargs['notice'])


if __name__ == '__main__':
    unittest.main()
