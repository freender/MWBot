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
        'OWNER': '1',
        'KUMA_HOST': 'http://kuma.local',
        'KUMA_LOGIN': 'user',
        'KUMA_PASSWORD': 'pass',
        'KUMA_MW_ID': '3',
        'WAF_TOKEN': 'waf-token',
        'WAF_ZONE': 'zone',
        'WAF_RULESET': 'ruleset',
        'WAF_RULEID': 'rule',
        'CDN_URL': 'example.com',
        'TELEGRAM_AUTH_USERS': '["1","2"]',
        'MW_BOT_ASN_DEFAULT': '1234',
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
        'modules.common',
        'modules.firewall',
        'modules.maintenance',
        'modules.redownload',
        'main',
    ]:
        sys.modules.pop(name, None)

    with mock.patch('telebot.TeleBot', DummyTeleBot):
        cfg = importlib.import_module('cfg')
        modules = importlib.import_module('modules')
        maintenance = importlib.import_module('modules.maintenance')
        setattr(maintenance, 'STATE_FILE', os.path.join(temp_dir, 'mw_state.json'))
        main = importlib.import_module('main')

    return cfg, modules, main


def button_texts(markup):
    return [button.text for row in markup.keyboard for button in row]


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
        self.assertNotIn('🔧 Maintenance', labels)

    def test_home_menu_shows_all_sections_for_owner(self):
        with mock.patch.object(self.main, '_show_menu') as show_menu:
            self.main._show_home_menu(10, user_id=10)

        labels = button_texts(show_menu.call_args.args[2])

        self.assertIn('📡 Plex Access', labels)
        self.assertIn('🎬 Media', labels)
        self.assertIn('🔧 Maintenance', labels)

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

    def test_nav_mw_rejects_authorized_non_owner(self):
        call = make_call(20, data='nav_mw')

        with mock.patch.object(self.main, '_show_maintenance_menu') as show_maintenance_menu, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_mw(call)

        show_maintenance_menu.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_nav_mw_allows_owner(self):
        call = make_call(10, data='nav_mw')

        with mock.patch.object(self.main, '_show_maintenance_menu') as show_maintenance_menu, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_nav_mw(call)

        show_maintenance_menu.assert_called_once_with(100, message_id=55)
        answer_callback_query.assert_called_once_with('call-id')

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

    def test_mw_status_rejects_authorized_non_owner(self):
        call = make_call(20, data='mw_status')

        with mock.patch.object(self.main, 'get_mw_status_text') as get_mw_status_text, \
             mock.patch.object(self.main, '_handle_mw_action') as handle_mw_action, \
             mock.patch.object(self.main, '_answer_not_allowed') as answer_not_allowed, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main._handle_mw_status(call)

        get_mw_status_text.assert_not_called()
        handle_mw_action.assert_not_called()
        answer_not_allowed.assert_called_once_with(100)
        answer_callback_query.assert_called_once_with('call-id')

    def test_mw_status_allows_owner(self):
        call = make_call(10, data='mw_status')

        with mock.patch.object(self.main, 'get_mw_status_text', return_value='mw text') as get_mw_status_text, \
             mock.patch.object(self.main, '_handle_mw_action') as handle_mw_action, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query, \
             mock.patch.object(self.main.bot, 'send_chat_action') as send_chat_action:
            self.main._handle_mw_status(call)

        get_mw_status_text.assert_called_once_with()
        send_chat_action.assert_called_once_with(100, 'typing')
        handle_mw_action.assert_called_once_with(call, 'mw text')
        answer_callback_query.assert_called_once_with('call-id')

    def test_redownload_issue_callback_rejects_unauthorized_user(self):
        call = make_call(30, data='redownload_issue:29')

        with mock.patch.object(self.main, 'resolve_redownload_issue') as resolve_redownload_issue, \
             mock.patch.object(self.main.bot, 'answer_callback_query') as answer_callback_query:
            self.main.handle_callback(call)

        resolve_redownload_issue.assert_not_called()
        answer_callback_query.assert_called_once_with('call-id', text='Not authorized')


if __name__ == '__main__':
    unittest.main()
