import importlib
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
        'KUMA_HOST': 'http://kuma.local',
        'KUMA_LOGIN': 'user',
        'KUMA_PASSWORD': 'pass',
        'KUMA_MW_ID': '3',
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
               description='ace has a failed systemd unit'):
    return {
        'labels': {'alertname': alertname, 'host': host, 'severity': severity},
        'annotations': {'description': description},
        'startsAt': '2026-08-09T05:06:00.000Z',
        'status': {},
    }


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
        self.assertIn('🔕 Alertmanager MW', labels)
        self.assertIn('🚨 New Incident', labels)

    def test_incident_command_always_opens_the_alert_picker(self):
        """Free text and replied-to text are ignored: alerts are the only incident source."""
        message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=10),
            text='/incident plex is down',
            reply_to_message=mock.Mock(text='CRITICAL: plex is missing', caption=None),
        )

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_start_incident_flow') as start_flow:
            self.main.command_incident(message)

        create_incident.assert_not_called()
        start_flow.assert_called_once_with(100, 10)

    def test_incident_button_is_owner_only(self):
        call = make_call(20, data='incident_new')

        with mock.patch.object(self.main, '_start_incident_flow') as start_flow, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main._handle_incident_new(call)

        start_flow.assert_not_called()
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
                 mock.patch.object(self.main, '_start_incident_flow') as start_flow, \
                 mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
                self.main.command_incident(message)

            create_incident.assert_not_called()
            start_flow.assert_not_called()
            answer_not_allowed.assert_called_once_with(100)

    def test_incident_flow_lists_firing_alerts(self):
        alerts = [make_alert(), make_alert(alertname='ContainerMissing', host='tower', severity='critical')]

        with mock.patch('modules.alertmanager.get_incident_alert_choices', return_value=alerts), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._start_incident_flow(100, 10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertIn('🟡 ace: SystemdUnitFailed', labels)
        self.assertIn('🔴 tower: ContainerMissing', labels)
        self.assertEqual(self.main._pending_incident_alerts['100:10'], alerts)

    def test_incident_flow_offers_no_free_text_escape_hatch(self):
        alerts = [make_alert()]

        with mock.patch('modules.alertmanager.get_incident_alert_choices', return_value=alerts), \
             mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._start_incident_flow(100, 10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertEqual(labels, ['🟡 ace: SystemdUnitFailed', '⬅ Back'])

    def test_incident_flow_stops_when_alertmanager_unavailable(self):
        with mock.patch('modules.alertmanager.get_incident_alert_choices', return_value=None), \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main._start_incident_flow(100, 10)

        self.assertIn('Alertmanager is unavailable', show_result.call_args.args[1])
        self.assertNotIn('100:10', self.main._pending_incident_alerts)

    def test_incident_flow_stops_when_nothing_is_firing(self):
        with mock.patch('modules.alertmanager.get_incident_alert_choices', return_value=[]), \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main._start_incident_flow(100, 10)

        self.assertIn('Nothing is firing', show_result.call_args.args[1])
        self.assertNotIn('100:10', self.main._pending_incident_alerts)

    def test_incident_alert_choice_creates_incident_from_alert(self):
        self.main._pending_incident_alerts['100:10'] = [make_alert()]
        call = make_call(10, data='incident_alert:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident:
            self.main.handle_callback(call)

        summary = create_incident.call_args.args[1]

        self.assertTrue(summary.startswith('SystemdUnitFailed on ace'))
        self.assertIn('ace has a failed systemd unit', summary)
        self.assertIn('- severity: warning', summary)
        self.assertNotIn('100:10', self.main._pending_incident_alerts)

    def test_incident_alert_choice_is_owner_only(self):
        self.main._pending_incident_alerts['100:20'] = [make_alert()]
        call = make_call(20, data='incident_alert:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main.handle_callback(call)

        create_incident.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)

    def test_incident_alert_choice_reports_expired_list(self):
        call = make_call(10, data='incident_alert:0')

        with mock.patch.object(self.main, '_create_incident_from_telegram') as create_incident, \
             mock.patch.object(self.main, '_show_incident_result') as show_result:
            self.main.handle_callback(call)

        create_incident.assert_not_called()
        self.assertIn('expired', show_result.call_args.args[1])

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

    def test_nav_alertmanager_mw_rejects_authorized_non_owner(self):
        call = make_call(20, data='nav_am_mw')

        with mock.patch.object(self.main, '_show_alertmanager_mw_menu') as show_menu, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed:
            self.main._handle_nav_alertmanager_mw(call)

        show_menu.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)

    def test_nav_alertmanager_mw_allows_owner(self):
        call = make_call(10, data='nav_am_mw')

        with mock.patch.object(self.main, '_show_alertmanager_mw_menu') as show_menu:
            self.main._handle_nav_alertmanager_mw(call)

        show_menu.assert_called_once_with(100, message_id=55)

    def test_alertmanager_mw_menu_renders_configured_duration(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_alertmanager_mw_menu(100)

        self.assertIn('12h', show_menu.call_args.args[1])

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
            self.main._handle_alertmanager_mw_status,
        ]

        with mock.patch.object(self.main, 'start_alertmanager_mw') as start_mw, \
             mock.patch.object(self.main, 'stop_alertmanager_mw') as stop_mw, \
             mock.patch.object(self.main, 'get_alertmanager_mw_status_text') as get_status:
            for handler in handlers:
                handler(call)

        start_mw.assert_not_called()
        stop_mw.assert_not_called()
        get_status.assert_not_called()

    def test_alertmanager_start_callback_allows_owner(self):
        call = make_call(10, data='am_mw_start')

        with mock.patch.object(self.main, 'start_alertmanager_mw', return_value='started') as start_mw, \
             mock.patch.object(self.main, '_handle_alertmanager_mw_action') as handle_action:
            self.main._handle_alertmanager_mw_start(call)

        start_mw.assert_called_once_with()
        handle_action.assert_called_once_with(call, 'started')


if __name__ == '__main__':
    unittest.main()
