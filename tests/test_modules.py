import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import mock


def load_modules_package(temp_dir):
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
    ]:
        sys.modules.pop(name, None)

    cfg = importlib.import_module('cfg')
    modules = importlib.import_module('modules')
    maintenance = importlib.import_module('modules.maintenance')
    redownload = importlib.import_module('modules.redownload')
    firewall = importlib.import_module('modules.firewall')
    setattr(maintenance, 'STATE_FILE', os.path.join(temp_dir, 'mw_state.json'))
    return cfg, modules, maintenance, redownload, firewall


class DummyBot:
    def __init__(self):
        self.deleted = []
        self.sent = []

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class ModulesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg, self.modules, self.maintenance, self.redownload, self.firewall = load_modules_package(self.temp_dir.name)
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': set(),
            'owner_chat_ids': set(),
            'loaded': False,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_duration(self):
        duration, error = self.modules.parse_duration('30m')
        self.assertEqual(duration, timedelta(minutes=30))
        self.assertIsNone(error)

        duration, error = self.modules.parse_duration('2h')
        self.assertEqual(duration, timedelta(hours=2))
        self.assertIsNone(error)

        duration, error = self.modules.parse_duration('bad')
        self.assertIsNone(duration)
        self.assertIn('Invalid duration', error)

    def test_command_metadata_exposes_menu_only_entrypoint(self):
        self.assertEqual(self.modules.DEFAULT_COMMANDS, {'start': 'Open main menu'})
        self.assertEqual(self.modules.AUTH_COMMANDS, {'start': 'Open main menu'})
        self.assertEqual(self.modules.OWNER_COMMANDS, {'start': 'Open main menu'})
        self.assertEqual(self.modules.COMMANDS, {'start': 'Open main menu'})
        self.assertNotIn('mw', self.modules.DEFAULT_COMMANDS)
        self.assertNotIn('help', self.modules.DEFAULT_COMMANDS)
        self.assertNotIn('ip', self.modules.AUTH_COMMANDS)
        self.assertNotIn('redownload', self.modules.AUTH_COMMANDS)
        self.assertNotIn('reset_ip', self.modules.OWNER_COMMANDS)

    def test_register_bot_commands_uses_default_auth_and_owner_scopes(self):
        bot = mock.Mock()
        access_cache = {
            'authorized_chat_ids': {2, 3},
            'owner_chat_ids': {3},
        }

        self.modules.register_bot_commands(bot, access_cache=access_cache)

        self.assertEqual(bot.set_my_commands.call_count, 3)

        default_call = bot.set_my_commands.call_args_list[0]
        self.assertEqual(
            [command.command for command in default_call.args[0]],
            ['start'],
        )
        self.assertEqual(default_call.kwargs['scope'].type, 'default')

        auth_call = bot.set_my_commands.call_args_list[1]
        self.assertEqual(
            [command.command for command in auth_call.args[0]],
            ['start'],
        )
        self.assertEqual(auth_call.kwargs['scope'].type, 'chat')
        self.assertEqual(auth_call.kwargs['scope'].chat_id, 2)

        owner_call = bot.set_my_commands.call_args_list[2]
        self.assertEqual(
            [command.command for command in owner_call.args[0]],
            ['start'],
        )
        self.assertEqual(owner_call.kwargs['scope'].type, 'chat')
        self.assertEqual(owner_call.kwargs['scope'].chat_id, 3)

    def test_parse_seerr_issue_url(self):
        issue_id, error = self.modules.parse_seerr_issue_url('https://seerr.example.com/issues/29')
        self.assertEqual(issue_id, 29)
        self.assertIsNone(error)

        issue_id, error = self.modules.parse_seerr_issue_url('not-a-url')
        self.assertIsNone(issue_id)
        self.assertIn('Invalid Seerr issue URL', error)

    def test_parse_seerr_reference_supports_issue_and_media_urls(self):
        reference, error = self.modules.parse_seerr_reference('https://seerr.example.com/issues/29')
        self.assertIsNone(error)
        self.assertEqual(reference, {'reference_type': 'issue', 'issue_id': 29})

        reference, error = self.modules.parse_seerr_reference('https://seerr.example.com/movie/1220564')
        self.assertIsNone(error)
        self.assertEqual(reference, {'reference_type': 'media', 'media_type': 'movie', 'tmdb_id': 1220564})

        reference, error = self.modules.parse_seerr_reference('https://seerr.example.com/tv/1408')
        self.assertIsNone(error)
        self.assertEqual(reference, {'reference_type': 'media', 'media_type': 'tv', 'tmdb_id': 1408})

    def test_parse_seerr_reference_accepts_urls_without_scheme(self):
        reference, error = self.modules.parse_seerr_reference('seerr.example.com/issues/29')
        self.assertIsNone(error)
        self.assertEqual(reference, {'reference_type': 'issue', 'issue_id': 29})

        reference, error = self.modules.parse_seerr_reference('seerr.example.com/movie/1220564')
        self.assertIsNone(error)
        self.assertEqual(reference, {'reference_type': 'media', 'media_type': 'movie', 'tmdb_id': 1220564})

    def test_resolve_redownload_issue_rejects_tv_media_urls(self):
        target, error = self.modules.resolve_redownload_issue('https://seerr.example.com/tv/1408')

        self.assertIsNone(target)
        self.assertEqual(error, 'TV replacements require an episode-linked Seerr issue URL.')

    def test_get_issue_target_movie(self):
        target, error = self.modules.get_issue_target({
            'subject': 'Movie title',
            'media': {'mediaType': 'movie', 'externalServiceId': 44},
        })

        self.assertIsNone(error)
        self.assertEqual(target['media_type'], 'movie')
        self.assertEqual(target['movie_id'], 44)

    def test_get_issue_target_prefers_4k_mapping_when_issue_points_to_4k(self):
        target, error = self.modules.get_issue_target({
            'subject': 'Movie title',
            'media': {
                'mediaType': 'movie',
                'externalServiceId': 44,
                'externalServiceId4k': 88,
                'serviceUrl': 'https://radarr4k.example.com/movie/123',
            },
        })

        self.assertIsNone(error)
        self.assertEqual(target['movie_id'], 88)
        self.assertTrue(target['is_4k'])

    def test_get_issue_target_episode_requires_specific_episode(self):
        target, error = self.modules.get_issue_target({
            'subject': 'Show',
            'problemSeason': 0,
            'problemEpisode': 0,
            'media': {'mediaType': 'tv', 'externalServiceId': 77},
        })

        self.assertIsNone(target)
        self.assertEqual(error, 'Seerr issue is not tied to a specific episode.')

    def test_resolve_redownload_issue(self):
        issue_payload = {
            'status': 1,
            'subject': 'Movie title',
            'media': {'mediaType': 'movie', 'externalServiceId': 44},
        }

        with mock.patch.object(self.redownload, 'get_seerr_issue', return_value=(issue_payload, None)), \
             mock.patch.object(self.redownload, 'resolve_movie_replacement', return_value=({'issue_id': 29, 'movie_id': 44, 'label': 'Movie title'}, None)):
            target, error = self.modules.resolve_redownload_issue('https://seerr.example.com/issues/29')

        self.assertIsNone(error)
        self.assertEqual(target['issue_id'], 29)
        self.assertEqual(target['movie_id'], 44)

    def test_resolve_redownload_issue_rejects_resolved_issue(self):
        issue_payload = {
            'status': 2,
            'subject': 'Movie title',
            'media': {'mediaType': 'movie', 'externalServiceId': 44},
        }

        with mock.patch.object(self.redownload, 'get_seerr_issue', return_value=(issue_payload, None)):
            target, error = self.modules.resolve_redownload_issue('https://seerr.example.com/issues/29')

        self.assertIsNone(target)
        self.assertIn('already resolved', error)

    def test_resolve_redownload_issue_from_media_url(self):
        issue_payload = {
            'id': 29,
            'status': 1,
            'subject': None,
            'updatedAt': '2026-03-10T00:00:00.000Z',
            'media': {'id': 4579, 'tmdbId': 1220564, 'mediaType': 'movie', 'externalServiceId': 44},
        }
        media_details = {
            'title': 'The Secret Agent',
            'mediaInfo': {'id': 4579, 'tmdbId': 1220564},
        }

        with mock.patch.object(self.redownload, 'find_seerr_issue_for_media', return_value=(issue_payload, media_details, None)), \
             mock.patch.object(self.redownload, 'resolve_movie_replacement', return_value=({'issue_id': 29, 'movie_id': 44, 'label': 'The Secret Agent'}, None)):
            target, error = self.modules.resolve_redownload_issue('https://seerr.example.com/movie/1220564')

        self.assertIsNone(error)
        self.assertEqual(target['issue_id'], 29)
        self.assertEqual(target['label'], 'The Secret Agent')

    def test_find_seerr_issue_for_media_returns_latest_matching_issue(self):
        media_details = {'title': 'Example Movie', 'mediaInfo': {'id': 4579, 'tmdbId': 1220564}}
        issue_older = {
            'id': 21,
            'updatedAt': '2026-03-01T00:00:00.000Z',
            'createdAt': '2026-03-01T00:00:00.000Z',
            'media': {'id': 4579, 'tmdbId': 1220564},
        }
        issue_newer = {
            'id': 29,
            'updatedAt': '2026-03-10T00:00:00.000Z',
            'createdAt': '2026-03-10T00:00:00.000Z',
            'media': {'id': 4579, 'tmdbId': 1220564},
        }

        with mock.patch.object(self.redownload, 'get_seerr_media_details', return_value=(media_details, None)), \
             mock.patch.object(self.redownload, 'get_all_seerr_issue_ids', return_value=[21, 29]), \
             mock.patch.object(self.redownload, 'get_seerr_issue', side_effect=[(issue_older, None), (issue_newer, None)]):
            issue, found_media_details, error = self.modules.find_seerr_issue_for_media('movie', 1220564)

        self.assertIsNone(error)
        self.assertEqual(issue['id'], 29)
        self.assertEqual(found_media_details['title'], 'Example Movie')

    def test_execute_redownload_movie_uses_queue_first(self):
        target = {'media_type': 'movie', 'movie_id': 44, 'label': 'Movie title', 'file_id': 700}
        responses = [
            [{'id': 501, 'movieId': 44}],
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses) as request_json:
            result = self.modules.execute_redownload(target)

        self.assertEqual(result, 'Blacklisted and removed queued movie release for Movie title.')
        delete_call = request_json.call_args_list[1]
        self.assertEqual(delete_call.args[0], 'DELETE')
        self.assertIn('skipRedownload', delete_call.kwargs['params'])

    def test_execute_redownload_movie_replaces_current_file(self):
        target = {'media_type': 'movie', 'movie_id': 44, 'label': 'Movie title', 'file_id': 700}
        responses = [
            [],
            {'records': [
                {'id': 802, 'movieId': 44, 'eventType': 'downloadFolderImported', 'downloadId': 'abc', 'sourceTitle': 'Release'},
                {'id': 801, 'movieId': 44, 'eventType': 'grabbed', 'downloadId': 'abc', 'sourceTitle': 'Release'},
            ]},
            None,
            None,
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses) as request_json:
            result = self.modules.execute_redownload(target)

        self.assertEqual(result, 'Blacklisted release, deleted the current file, and triggered a fresh search for Movie title.')
        post_call = request_json.call_args_list[2]
        self.assertEqual(post_call.args[0], 'POST')
        self.assertIn('/api/v3/history/failed/801', post_call.args[1])
        delete_file_call = request_json.call_args_list[3]
        self.assertEqual(delete_file_call.args[0], 'DELETE')
        self.assertIn('/api/v3/moviefile/700', delete_file_call.args[1])
        search_call = request_json.call_args_list[4]
        self.assertEqual(search_call.args[0], 'POST')
        self.assertEqual(search_call.kwargs['payload']['name'], 'MoviesSearch')

    def test_execute_redownload_episode_replaces_current_file(self):
        target = {
            'media_type': 'episode',
            'series_id': 77,
            'season_number': 1,
            'episode_number': 2,
            'episode_id': 9001,
            'file_id': 444,
            'label': 'Show S01E02',
        }
        responses = [
            [],
            {'records': [
                {'id': 9011, 'episodeId': 9001, 'eventType': 'downloadFolderImported', 'downloadId': 'xyz', 'sourceTitle': 'Episode Release'},
                {'id': 9010, 'episodeId': 9001, 'eventType': 'grabbed', 'downloadId': 'xyz', 'sourceTitle': 'Episode Release'},
            ]},
            None,
            None,
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses):
            result = self.modules.execute_redownload(target)

        self.assertEqual(result, 'Blacklisted release, deleted the current file, and triggered a fresh search for Show S01E02.')

    def test_execute_redownload_resolves_seerr_issue_on_success(self):
        target = {'media_type': 'movie', 'movie_id': 44, 'label': 'Movie title', 'file_id': 700, 'issue_id': 29}
        responses = [
            [{'id': 501, 'movieId': 44}],
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses), \
             mock.patch.object(self.redownload, 'post_seerr_issue_comment', return_value=(True, None)) as mock_comment, \
             mock.patch.object(self.redownload, 'resolve_seerr_issue', return_value=(True, None)) as mock_resolve:
            result = self.modules.execute_redownload(target)

        mock_comment.assert_called_once_with(29, self.redownload.AUTO_RESOLVE_COMMENT)
        mock_resolve.assert_called_once_with(29)
        self.assertIn('Blacklisted', result)
        self.assertIn('issue #29 has been resolved', result)

    def test_execute_redownload_reports_resolve_failure(self):
        target = {'media_type': 'movie', 'movie_id': 44, 'label': 'Movie title', 'file_id': 700, 'issue_id': 29}
        responses = [
            [{'id': 501, 'movieId': 44}],
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses), \
             mock.patch.object(self.redownload, 'post_seerr_issue_comment', return_value=(True, None)), \
             mock.patch.object(self.redownload, 'resolve_seerr_issue', return_value=(False, 'Failed to resolve Seerr issue (status 500).')):
            result = self.modules.execute_redownload(target)

        self.assertIn('Blacklisted', result)
        self.assertIn('Warning:', result)

    def test_execute_redownload_reports_comment_failure(self):
        target = {'media_type': 'movie', 'movie_id': 44, 'label': 'Movie title', 'file_id': 700, 'issue_id': 29}
        responses = [
            [{'id': 501, 'movieId': 44}],
            None,
        ]

        with mock.patch.object(self.redownload, 'request_json', side_effect=responses), \
             mock.patch.object(self.redownload, 'post_seerr_issue_comment', return_value=(False, 'Failed to comment on Seerr issue (status 500).')), \
             mock.patch.object(self.redownload, 'resolve_seerr_issue', return_value=(True, None)):
            result = self.modules.execute_redownload(target)

        self.assertIn('Blacklisted', result)
        self.assertIn('Failed to comment on Seerr issue', result)
        self.assertIn('issue #29 has been resolved', result)

    def test_post_seerr_issue_comment_posts_expected_message(self):
        with mock.patch.object(self.redownload, 'request_json', return_value=None) as mock_request:
            success, error = self.redownload.post_seerr_issue_comment(29, self.redownload.AUTO_RESOLVE_COMMENT)

        self.assertTrue(success)
        self.assertIsNone(error)
        mock_request.assert_called_once_with(
            'POST',
            'https://seerr.example.com/api/v1/issue/29/comment',
            headers={'X-Api-Key': 'seerr-key', 'Content-Type': 'application/json'},
            payload={'message': self.redownload.AUTO_RESOLVE_COMMENT},
        )

    def test_is_issue_open(self):
        self.assertTrue(self.modules.is_issue_open({'status': 1}))
        self.assertFalse(self.modules.is_issue_open({'status': 2}))
        self.assertFalse(self.modules.is_issue_open({'status': None}))
        self.assertFalse(self.modules.is_issue_open({}))

    def test_select_failed_history_record_prefers_grabbed_events(self):
        record = self.modules.select_failed_history_record([
            {'id': 1, 'movieId': 44, 'eventType': 'downloadFolderImported'},
            {'id': 2, 'movieId': 44, 'eventType': 'grabbed'},
            {'id': 3, 'movieId': 44, 'eventType': 'downloadFailed'},
        ], 'movieId', 44)

        self.assertEqual(record['id'], 2)

    def test_build_issue_label(self):
        self.assertEqual(
            self.modules.build_issue_label({
                'id': 5,
                'issueType': 1,
                'display_title': 'Bad Movie',
                'display_year': '2016',
                'media': {'mediaType': 'movie'},
            }),
            'Bad Movie (2016) - Video',
        )
        self.assertEqual(
            self.modules.build_issue_label({'id': 5, 'issueType': 2, 'display_title': 'Show Name', 'media': {'mediaType': 'tv'}, 'problemSeason': 2, 'problemEpisode': 3}),
            'Show Name S02E03 - Audio',
        )
        self.assertEqual(
            self.modules.build_issue_label({
                'id': 5,
                'issueType': 4,
                'display_title': 'La La Land',
                'display_year': '2016',
                'media': {'mediaType': 'movie'},
            }),
            'La La Land (2016)',
        )
        self.assertEqual(
            self.modules.build_issue_label({'id': 5, 'media': {'mediaType': 'movie', 'tmdbId': 550}}),
            'Movie #550',
        )
        self.assertEqual(
            self.modules.build_issue_label({'id': 5, 'issueType': 3, 'media': {'mediaType': 'tv', 'tmdbId': 2316}, 'problemSeason': 1, 'problemEpisode': 7}),
            'Series #2316 S01E07 - Subtitles',
        )

    def test_get_open_seerr_issues_filters_and_enriches_titles(self):
        issue_payload = {
            'results': [
                {'id': 11, 'status': 1, 'issueType': 1, 'media': {'mediaType': 'movie', 'tmdbId': 550, 'externalServiceId': 44}},
                {'id': 12, 'status': 1, 'issueType': 2, 'media': {'mediaType': 'tv', 'tmdbId': 2316, 'externalServiceId': 77}, 'problemSeason': 1, 'problemEpisode': 2},
                {'id': 13, 'status': 1, 'issueType': 4, 'media': {'mediaType': 'tv', 'tmdbId': 2316, 'externalServiceId': 77}},
            ],
            'pageInfo': {'pages': 1},
        }

        with mock.patch.object(self.redownload, 'request_json', return_value=issue_payload), \
             mock.patch.object(self.redownload, 'get_seerr_media_details', side_effect=[({'title': 'Fight Club'}, None), ({'name': 'The Office'}, None)]):
            issues = self.modules.get_open_seerr_issues()

        self.assertEqual([issue['id'] for issue in issues], [12, 11])
        self.assertEqual(issues[0]['display_title'], 'The Office')
        self.assertEqual(issues[1]['display_title'], 'Fight Club')

    def test_build_redownload_confirmation(self):
        text = self.modules.build_redownload_confirmation({'media_type': 'movie', 'label': 'Movie title', 'issue_id': 29, 'file_path': '/movies/Movie title.mkv', 'service': 'Radarr'})
        self.assertIn('Movie title', text)
        self.assertIn('<b>Ready to replace</b>', text)
        self.assertIn('<b>Issue:</b> #29', text)
        self.assertIn('<b>Current file:</b> <code>/movies/Movie title.mkv</code>', text)
        self.assertIn('delete current file', text)

    def test_build_redownload_confirmation_warns_for_non_english_original_language(self):
        text = self.modules.build_redownload_confirmation({
            'media_type': 'movie',
            'label': 'Movie title',
            'issue_id': 29,
            'file_path': '/movies/Movie title.mkv',
            'service': 'Radarr',
            'original_language_name': 'French',
        })

        self.assertIn('<b>Warning:</b> original language is <b>French</b>.', text)
        self.assertIn('may not be available in English at all', text)
        self.assertIn('Only continue if you still want to replace it.', text)

    def test_is_auth_user_accepts_seerr_telegram_chat_id(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '733172269'},
            {'telegramChatId': '987654321'},
        ]
        message = mock.Mock(
            chat=mock.Mock(id=999),
            from_user=mock.Mock(id=987654321),
        )

        with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
            self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_auth_user(message))

    def test_is_owner_uses_seerr_owner_telegram_chat_id(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '733172269'},
            {'telegramChatId': '987654321'},
        ]
        owner_message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=733172269),
        )
        user_message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=987654321),
        )

        with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
            self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_owner(owner_message))
        self.assertFalse(self.modules.is_owner(user_message))

    def test_warm_seerr_access_cache_can_be_forced_to_env_only(self):
        message = mock.Mock(
            chat=mock.Mock(id=2),
            from_user=mock.Mock(id=2),
        )

        with mock.patch.object(self.cfg, 'SEERR_ACCESS_ENV_ONLY', True, create=True):
            with mock.patch.object(self.modules, 'request_json') as request_json:
                cache = self.modules.warm_seerr_access_cache()

        self.assertTrue(cache['loaded'])
        self.assertTrue(self.modules.is_auth_user(message))
        request_json.assert_not_called()

    def test_warm_seerr_access_cache_can_force_owner_to_authorized_only(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '733172269'},
            {'telegramChatId': '987654321'},
        ]
        owner_message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=733172269),
        )

        with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_USER_ID', 733172269, create=True):
            with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_MODE', 'authorized', create=True):
                with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
                    self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_auth_user(owner_message))
        self.assertFalse(self.modules.is_owner(owner_message))

    def test_warm_seerr_access_cache_can_force_user_to_unauthorized(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '733172269'},
            {'telegramChatId': '987654321'},
        ]
        user_message = mock.Mock(
            chat=mock.Mock(id=100),
            from_user=mock.Mock(id=987654321),
        )

        with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_USER_ID', 987654321, create=True):
            with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_MODE', 'unauthorized', create=True):
                with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
                    self.modules.warm_seerr_access_cache()

        self.assertFalse(self.modules.is_auth_user(user_message))
        self.assertFalse(self.modules.is_owner(user_message))

    def test_warm_seerr_access_cache_falls_back_to_env(self):
        payload = {'results': [{'id': 1}], 'pageInfo': {'results': 1}}
        message = mock.Mock(
            chat=mock.Mock(id=2),
            from_user=mock.Mock(id=2),
        )

        with mock.patch.object(self.modules, 'request_json', side_effect=RuntimeError('boom')):
            cache = self.modules.warm_seerr_access_cache()

        self.assertTrue(cache['loaded'])
        self.assertTrue(self.modules.is_auth_user(message))

    def test_mw_status_text(self):
        state = self.modules.build_mw_state(timedelta(minutes=45), reason='Firmware maintenance')
        text = self.modules.get_mw_status_text(state)
        self.assertIn('Firmware maintenance is active.', text)
        self.assertIn('Remaining:', text)

    def test_stop_timed_mw_clears_state_and_deletes_message(self):
        state = self.modules.build_mw_state(
            timedelta(minutes=30),
            notify_chat_id='200',
            notify_message_id=55,
            reason='Maintenance window',
        )
        self.modules.save_mw_state(state)
        bot = DummyBot()

        with mock.patch.object(self.maintenance, 'stop_mw', return_value='MW has been completed'):
            result, success = self.modules.stop_timed_mw(bot)

        self.assertTrue(success)
        self.assertEqual(result, 'MW has been completed')
        self.assertEqual(bot.deleted, [('200', 55)])
        self.assertIsNone(self.modules.load_mw_state())

    def test_stop_timed_mw_keeps_state_on_failure(self):
        state = self.modules.build_mw_state(timedelta(minutes=30), notify_chat_id='200', notify_message_id=55)
        self.modules.save_mw_state(state)
        bot = DummyBot()

        with mock.patch.object(self.maintenance, 'stop_mw', return_value='Unable to establish connection to Uptime Kuma'):
            result, success = self.modules.stop_timed_mw(bot)

        self.assertFalse(success)
        self.assertEqual(result, 'Unable to establish connection to Uptime Kuma')
        self.assertEqual(bot.deleted, [])
        self.assertIsNotNone(self.modules.load_mw_state())

    def test_replace_mw_state_deletes_previous_message(self):
        self.modules.save_mw_state(self.modules.build_mw_state(
            timedelta(minutes=30),
            notify_chat_id='200',
            notify_message_id=55,
        ))
        bot = DummyBot()

        new_state = self.modules.build_mw_state(
            timedelta(minutes=45),
            notify_chat_id='200',
            notify_message_id=77,
        )
        self.modules.replace_mw_state(bot, new_state)

        self.assertEqual(bot.deleted, [('200', 55)])
        self.assertEqual(self.modules.load_mw_state()['notify_message_id'], 77)

    def test_maintain_timed_mw_sends_failure_message(self):
        expired_at = datetime.now(ZoneInfo(self.cfg.TZ)) - timedelta(minutes=1)
        self.modules.save_mw_state({
            'expires_at': expired_at.isoformat(),
            'duration': '30m',
            'notify_chat_id': '200',
            'notify_message_id': 55,
            'reason': 'Maintenance window',
        })
        bot = DummyBot()

        with mock.patch.object(self.maintenance, 'stop_timed_mw', return_value=('boom', False)):
            with mock.patch.object(self.maintenance.time, 'sleep', side_effect=RuntimeError('stop loop')):
                with self.assertRaises(RuntimeError):
                    self.modules.maintain_timed_mw(bot, poll_interval=0)

        self.assertEqual(bot.sent, [('200', 'Timed maintenance cleanup failed: boom')])

    def test_is_valid_ip_uses_stdlib_parser(self):
        self.assertTrue(self.modules.is_valid_ip('127.0.0.1'))
        self.assertTrue(self.modules.is_valid_ip('::1'))
        self.assertFalse(self.modules.is_valid_ip('999.999.999.999'))
        self.assertFalse(self.modules.is_valid_ip(None))
        self.assertFalse(self.modules.is_valid_ip(''))

    def test_get_rule_status_uses_shared_rule_fetch(self):
        payload = {
            'result': {
                'rules': [
                    {'id': 'rule', 'enabled': True, 'last_updated': '2026-03-10T00:00:00.000Z'},
                ]
            }
        }

        with mock.patch.object(self.firewall, 'request_json', return_value=payload):
            enabled, error = self.modules.get_rule_status()

        self.assertTrue(enabled)
        self.assertIsNone(error)

    def test_get_firewall_status_text_returns_disabled_when_rule_off(self):
        with mock.patch.object(self.firewall, 'get_rule_status', return_value=(False, None)):
            with mock.patch.object(self.firewall, 'get_asns_from_firewall_rule') as get_asns:
                status = self.modules.get_firewall_status_text()

        self.assertEqual(status, 'Plex access is disabled.')
        get_asns.assert_not_called()

    def test_get_firewall_status_text_lists_temporary_asns(self):
        with mock.patch.object(self.firewall, 'get_rule_status', return_value=(True, None)):
            with mock.patch.object(self.firewall, 'get_asns_from_firewall_rule', return_value=(['1234', '7922'], None)):
                status = self.modules.get_firewall_status_text()

        self.assertEqual(status, 'Plex access is enabled. Temporary ASNs: 7922.')

    def test_get_asn_from_ip_parses_json_response(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'as': 'AS7922 Comcast Cable'}

        with mock.patch.object(self.firewall.requests, 'get', return_value=response):
            asn, error = self.modules.get_asn_from_ip('127.0.0.1')

        self.assertEqual(asn, '7922')
        self.assertIsNone(error)

    def test_get_asn_from_ip_rejects_missing_asn(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'as': ''}

        with mock.patch.object(self.firewall.requests, 'get', return_value=response):
            asn, error = self.modules.get_asn_from_ip('127.0.0.1')

        self.assertIsNone(asn)
        self.assertIn('ASN for this IP is not found', error)

    def test_get_next_firewall_run_uses_same_day_when_before_window(self):
        current_time = datetime(2026, 3, 10, 1, 15, tzinfo=ZoneInfo('UTC'))
        next_run = self.modules.get_next_firewall_run(current_time)

        self.assertEqual(next_run, datetime(2026, 3, 10, 3, 40, tzinfo=ZoneInfo('UTC')))

    def test_get_next_firewall_run_rolls_to_next_day_after_window(self):
        current_time = datetime(2026, 3, 10, 4, 0, tzinfo=ZoneInfo('UTC'))
        next_run = self.modules.get_next_firewall_run(current_time)

        self.assertEqual(next_run, datetime(2026, 3, 11, 3, 40, tzinfo=ZoneInfo('UTC')))

    def test_cfg_missing_required_variable_raises_helpful_error(self):
        os.environ.pop('TOKEN', None)
        sys.modules.pop('cfg', None)
        with self.assertRaisesRegex(RuntimeError, 'Missing required environment variable: TOKEN'):
            importlib.import_module('cfg')
        os.environ['TOKEN'] = 'token'


if __name__ == '__main__':
    unittest.main()
