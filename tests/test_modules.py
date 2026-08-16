import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from unittest import mock


def load_modules_package(temp_dir):
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
    ]:
        sys.modules.pop(name, None)

    cfg = importlib.import_module('cfg')
    modules = importlib.import_module('modules')
    maintenance = importlib.import_module('modules.maintenance')
    redownload = importlib.import_module('modules.redownload')
    firewall = importlib.import_module('modules.firewall')
    network_check = importlib.import_module('modules.network_check')
    setattr(maintenance, 'STATE_FILE', os.path.join(temp_dir, 'mw_state.json'))
    setattr(maintenance, 'ALERTMANAGER_STATE_FILE', os.path.join(temp_dir, 'alertmanager_mw_state.json'))
    return cfg, modules, maintenance, redownload, firewall, network_check


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
        self.cfg, self.modules, self.maintenance, self.redownload, self.firewall, self.network_check = load_modules_package(self.temp_dir.name)
        self.firewall.AS_ORGANIZATIONS_FILE = os.path.join(self.temp_dir.name, 'as_organizations.json')
        self.modules._seerr_access_cache.update({
            'authorized_chat_ids': set(),
            'owner_chat_ids': set(),
            'loaded': False,
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_format_duration(self):
        self.assertEqual(self.modules.format_duration(timedelta(minutes=30)), '30m')
        self.assertEqual(self.modules.format_duration(timedelta(hours=2)), '2h')
        self.assertEqual(self.modules.format_duration(timedelta(hours=4, minutes=12)), '4h 12m')
        self.assertEqual(self.modules.format_duration(timedelta(seconds=-5)), '0m')

    def test_format_duration_renders_days(self):
        """Without this a week-long silence labels its button '168h'."""
        self.assertEqual(self.modules.format_duration(timedelta(days=1)), '1d')
        self.assertEqual(self.modules.format_duration(timedelta(days=7)), '7d')
        self.assertEqual(self.modules.format_duration(timedelta(days=1, hours=6)), '1d 6h')

    def test_format_remaining_uses_the_coarsest_unit(self):
        self.assertEqual(self.modules.format_remaining(timedelta(days=6, hours=23)), '6d')
        self.assertEqual(self.modules.format_remaining(timedelta(hours=5, minutes=59)), '5h')
        self.assertEqual(self.modules.format_remaining(timedelta(minutes=12)), '12m')
        self.assertEqual(self.modules.format_remaining(timedelta(seconds=-5)), '1m')

    def test_duration_config_accepts_days(self):
        """`1d` in the env must parse; cfg is evaluated at import, so a raise crash-loops."""
        self.assertEqual(self.cfg._parse_duration('X', '7d'), timedelta(days=7))
        self.assertEqual(self.cfg._parse_duration('X', '30m'), timedelta(minutes=30))
        self.assertEqual(self.cfg._parse_duration('X', '12h'), timedelta(hours=12))

    def test_duration_config_rejects_nonsense(self):
        for value in ('', 'd', '0d', '-1d', '7w', 'abc', '7'):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):
                    self.cfg._parse_duration('X', value)

    def test_silence_duration_choices_parse_in_button_order(self):
        with mock.patch.dict(os.environ, {'X': '1d,3d,7d'}):
            self.assertEqual(
                self.cfg._get_durations('X', '2h'),
                [timedelta(days=1), timedelta(days=3), timedelta(days=7)],
            )

    def test_silence_duration_choices_reject_an_empty_list(self):
        with mock.patch.dict(os.environ, {'X': ' , '}):
            with self.assertRaises(RuntimeError):
                self.cfg._get_durations('X', '1d')

    def test_command_metadata_exposes_menu_only_entrypoint(self):
        self.assertEqual(self.modules.DEFAULT_COMMANDS, {'start': 'Open main menu'})
        self.assertEqual(self.modules.AUTH_COMMANDS, {'start': 'Open main menu'})
        self.assertEqual(self.modules.OWNER_COMMANDS, {
            'start': 'Open main menu',
            'incident': 'Create a homelab incident',
        })
        self.assertEqual(self.modules.COMMANDS, self.modules.OWNER_COMMANDS)
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
            ['start', 'incident'],
        )
        self.assertEqual(owner_call.kwargs['scope'].type, 'chat')
        self.assertEqual(owner_call.kwargs['scope'].chat_id, 3)

    def test_build_incident_title_uses_first_line_and_caps_length(self):
        self.assertEqual(self.modules.build_incident_title('  ALERT: plex is down\nmore'), 'ALERT: plex is down')
        self.assertEqual(len(self.modules.build_incident_title('x' * 150)), 100)

    def test_build_incident_body_carries_only_the_alert(self):
        body = self.modules.build_incident_body('SystemdUnitFailed on ace\n\ndetail line')

        self.assertTrue(body.startswith('## Alert\n'))
        self.assertIn('detail line', body)
        self.assertNotIn('Replied-To', body)
        self.assertIn('<!-- incident-source: telegram-alert -->', body)

    def test_build_incident_body_marks_the_alert_fingerprint(self):
        # GITHUB_INCIDENT_REPO parses this marker to dedup filings and to correlate a closed
        # incident back to its alert. See AGENTS.md "Incident Pipeline Contract".
        body = self.modules.build_incident_body('SystemdUnitFailed on ace', fingerprint='a1b2c3d4e5f60718')

        self.assertIn('<!-- alert-fingerprint: a1b2c3d4e5f60718 -->', body)

    def test_build_incident_body_keeps_the_marker_after_a_long_alert(self):
        incidents = importlib.import_module('modules.incidents')
        body = self.modules.build_incident_body('x' * 20000, fingerprint='a1b2c3d4e5f60718')

        self.assertTrue(body.rstrip().endswith('<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'))
        self.assertLess(len(body), incidents.MAX_INCIDENT_TEXT_LENGTH + 1000)

    def test_build_incident_body_omits_a_malformed_fingerprint(self):
        # The value is interpolated into a comment another repository parses; anything that
        # is not fingerprint-shaped is dropped rather than written through.
        body = self.modules.build_incident_body('alert', fingerprint='not a fingerprint -->')

        self.assertNotIn('alert-fingerprint', body)

    def test_triage_trigger_comment_keeps_the_cross_repo_token(self):
        # The triage workflow lives in GITHUB_INCIDENT_REPO and gates on a leading '/oc'
        # token. Dropping the token disables triage in that repo with no failure visible
        # from here. See AGENTS.md "Incident Pipeline Contract".
        #
        # The trailing space is load-bearing, which is why this asserts '/oc ' and not
        # '/oc'. opencode derives the model's prompt from this comment body: when the body
        # is *exactly* a trigger token it substitutes its own canned prompt -- literally
        # "Summarize this thread" -- and only otherwise passes the body through. A bare
        # '/oc' therefore still triages successfully and still posts a report, but the
        # agent was asked to summarise a thread rather than investigate an incident. Green
        # and wrong is the worst failure available here, so keep text after the token.
        # Rewording that text is safe; deleting it is not.
        incidents = importlib.import_module('modules.incidents')

        self.assertTrue(incidents.TRIAGE_TRIGGER_COMMENT.startswith('/oc '))

    def test_create_incident_opens_issue_and_requests_triage(self):
        response = mock.Mock(status_code=201)
        response.json.return_value = {
            'number': 7,
            'title': 'plex is down',
            'html_url': 'https://github.com/freender/homelab-ops/issues/7',
        }
        incidents = importlib.import_module('modules.incidents')

        with mock.patch.object(incidents.requests, 'post', return_value=response) as post:
            incident, error = self.modules.create_incident('plex is down on tower')

        self.assertIsNone(error)
        self.assertEqual(incident['number'], 7)
        self.assertNotIn('warning', incident)
        self.assertEqual(post.call_count, 2)

        issue_call, comment_call = post.call_args_list
        self.assertEqual(issue_call.kwargs['json']['labels'], ['incident'])
        self.assertNotIn('github-token', str(issue_call.kwargs['json']))
        self.assertTrue(comment_call.args[0].endswith('/issues/7/comments'))
        self.assertTrue(comment_call.kwargs['json']['body'].startswith('/oc '))

    def test_create_incident_reuses_the_open_incident_for_the_same_alert(self):
        incidents = importlib.import_module('modules.incidents')
        listing = mock.Mock(status_code=200)
        listing.json.return_value = [
            {'number': 4, 'title': 'other', 'html_url': 'u4', 'body': 'no marker here'},
            {
                'number': 5,
                'title': 'SystemdUnitFailed on ace',
                'html_url': 'https://github.com/freender/homelab-ops/issues/5',
                'body': 'body\n<!-- alert-fingerprint: a1b2c3d4e5f60718 -->',
            },
        ]

        with mock.patch.object(incidents.requests, 'get', return_value=listing), \
                mock.patch.object(incidents.requests, 'post') as post:
            incident, error = self.modules.create_incident('alert', fingerprint='a1b2c3d4e5f60718')

        self.assertIsNone(error)
        self.assertEqual(incident['number'], 5)
        self.assertTrue(incident['duplicate'])
        # Nothing filed and, just as importantly, no second triage run requested.
        post.assert_not_called()

    def test_create_incident_files_when_no_open_incident_matches(self):
        incidents = importlib.import_module('modules.incidents')
        listing = mock.Mock(status_code=200)
        listing.json.return_value = [
            {'number': 5, 'title': 'x', 'html_url': 'u5', 'body': '<!-- alert-fingerprint: ffffffffffffffff -->'},
        ]
        created = mock.Mock(status_code=201)
        created.json.return_value = {
            'number': 8,
            'title': 'plex is down',
            'html_url': 'https://github.com/freender/homelab-ops/issues/8',
        }

        with mock.patch.object(incidents.requests, 'get', return_value=listing), \
                mock.patch.object(incidents.requests, 'post', return_value=created) as post:
            incident, error = self.modules.create_incident('alert', fingerprint='a1b2c3d4e5f60718')

        self.assertIsNone(error)
        self.assertEqual(incident['number'], 8)
        self.assertNotIn('duplicate', incident)
        self.assertIn('<!-- alert-fingerprint: a1b2c3d4e5f60718 -->', post.call_args_list[0].kwargs['json']['body'])

    def test_create_incident_files_when_the_dedup_lookup_fails(self):
        # Failing open risks one duplicate issue; failing closed would silently drop a real
        # incident. The first is recoverable by hand, the second is not.
        incidents = importlib.import_module('modules.incidents')
        created = mock.Mock(status_code=201)
        created.json.return_value = {
            'number': 9,
            'title': 'plex is down',
            'html_url': 'https://github.com/freender/homelab-ops/issues/9',
        }

        with mock.patch.object(incidents.requests, 'get', side_effect=incidents.requests.RequestException), \
                mock.patch.object(incidents.requests, 'post', return_value=created):
            incident, error = self.modules.create_incident('alert', fingerprint='a1b2c3d4e5f60718')

        self.assertIsNone(error)
        self.assertEqual(incident['number'], 9)

    def test_find_open_incident_skips_pull_requests(self):
        incidents = importlib.import_module('modules.incidents')
        listing = mock.Mock(status_code=200)
        listing.json.return_value = [{
            'number': 11,
            'html_url': 'u11',
            'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->',
            'pull_request': {'url': 'p'},
        }]

        with mock.patch.object(incidents.requests, 'get', return_value=listing):
            self.assertIsNone(incidents.find_open_incident('a1b2c3d4e5f60718'))

    def _incident_listing(self, issues):
        listing = mock.Mock(status_code=200)
        listing.json.return_value = issues
        return listing

    def test_incident_index_maps_every_open_incident_by_fingerprint(self):
        incidents = importlib.import_module('modules.incidents')
        listing = self._incident_listing([
            {'number': 5, 'title': 'a', 'html_url': 'u5',
             'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'},
            {'number': 6, 'title': 'b', 'html_url': 'u6',
             'body': '<!-- alert-fingerprint: ffffffffffffffff -->'},
            {'number': 7, 'title': 'c', 'html_url': 'u7', 'body': 'no marker'},
        ])

        with mock.patch.object(incidents.requests, 'get', return_value=listing) as get:
            index = incidents.get_open_incident_index()

        # One listing for the whole alert list, not one lookup per alert.
        self.assertEqual(get.call_count, 1)
        self.assertEqual(sorted(index), ['a1b2c3d4e5f60718', 'ffffffffffffffff'])
        self.assertEqual(index['a1b2c3d4e5f60718']['number'], 5)

    def test_incident_index_ignores_a_malformed_fingerprint_marker(self):
        incidents = importlib.import_module('modules.incidents')
        listing = self._incident_listing([
            {'number': 5, 'title': 'a', 'html_url': 'u5',
             'body': '<!-- alert-fingerprint: not-a-fingerprint -->'},
        ])

        with mock.patch.object(incidents.requests, 'get', return_value=listing):
            self.assertEqual(incidents.get_open_incident_index(), {})

    def test_incident_index_is_empty_when_github_is_unreachable(self):
        """A GitHub blip costs the alert list its annotations, not the list."""
        incidents = importlib.import_module('modules.incidents')

        with mock.patch.object(incidents.requests, 'get', side_effect=incidents.requests.RequestException):
            self.assertEqual(incidents.get_open_incident_index(use_cache=True), {})

    def test_incident_index_does_not_cache_a_failed_fetch(self):
        incidents = importlib.import_module('modules.incidents')
        listing = self._incident_listing([
            {'number': 5, 'title': 'a', 'html_url': 'u5',
             'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'},
        ])

        with mock.patch.object(incidents.requests, 'get', side_effect=incidents.requests.RequestException):
            self.assertEqual(incidents.get_open_incident_index(use_cache=True), {})
        with mock.patch.object(incidents.requests, 'get', return_value=listing):
            self.assertIn('a1b2c3d4e5f60718', incidents.get_open_incident_index(use_cache=True))

    def test_incident_index_cache_absorbs_repeat_renders(self):
        incidents = importlib.import_module('modules.incidents')
        listing = self._incident_listing([
            {'number': 5, 'title': 'a', 'html_url': 'u5',
             'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'},
        ])

        with mock.patch.object(incidents.requests, 'get', return_value=listing) as get:
            incidents.get_open_incident_index(use_cache=True)
            incidents.get_open_incident_index(use_cache=True)

        self.assertEqual(get.call_count, 1)

    def test_dedup_never_reads_the_cache(self):
        """A cached "not filed" is exactly the stale answer that files a duplicate."""
        incidents = importlib.import_module('modules.incidents')
        empty = self._incident_listing([])
        filed = self._incident_listing([
            {'number': 5, 'title': 'a', 'html_url': 'u5',
             'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'},
        ])

        with mock.patch.object(incidents.requests, 'get', return_value=empty):
            incidents.get_open_incident_index(use_cache=True)
        with mock.patch.object(incidents.requests, 'get', return_value=filed) as get:
            found = incidents.find_open_incident('a1b2c3d4e5f60718')

        get.assert_called_once()
        self.assertEqual(found['number'], 5)
        self.assertTrue(found['duplicate'])

    def test_filing_an_incident_invalidates_the_index_cache(self):
        """The list is what you return to right after filing; it must show the new number."""
        incidents = importlib.import_module('modules.incidents')
        created = mock.Mock(status_code=201)
        created.json.return_value = {
            'number': 8, 'title': 'x',
            'html_url': 'https://github.com/freender/homelab-ops/issues/8',
        }
        filed = self._incident_listing([
            {'number': 8, 'title': 'x', 'html_url': 'u8',
             'body': '<!-- alert-fingerprint: a1b2c3d4e5f60718 -->'},
        ])

        with mock.patch.object(incidents.requests, 'get', return_value=self._incident_listing([])), \
                mock.patch.object(incidents.requests, 'post', return_value=created):
            incidents.get_open_incident_index(use_cache=True)
            self.modules.create_incident('alert', fingerprint='a1b2c3d4e5f60718')

        with mock.patch.object(incidents.requests, 'get', return_value=filed):
            self.assertIn('a1b2c3d4e5f60718', incidents.get_open_incident_index(use_cache=True))

    def _report_comment(self, **overrides):
        comment = {
            'id': 900,
            'created_at': '2026-08-11T10:00:00Z',
            'user': {'login': 'github-actions[bot]'},
            'issue_url': 'https://api.github.com/repos/freender/homelab-ops/issues/12',
            'html_url': 'https://github.com/freender/homelab-ops/issues/12#issuecomment-900',
            'body': '## Verdict\n\nbackup-local points at a retired host.\n\n## Why\n\n...',
        }
        comment.update(overrides)
        return comment

    def _triage_reports(self, comments, state=None):
        """Run the watcher against a canned comment listing and a temp state file."""
        incidents = importlib.import_module('modules.incidents')
        state_path = os.path.join(self.temp_dir.name, 'triage_reports.json')
        if state is not None:
            with open(state_path, 'w', encoding='utf-8') as handle:
                json.dump(state, handle)
        response = mock.Mock(status_code=200)
        response.json.return_value = comments
        with mock.patch.object(incidents, 'TRIAGE_STATE_FILE', state_path), \
                mock.patch.object(incidents.requests, 'get', return_value=response) as get:
            found = incidents.find_triage_reports()
        with open(state_path, encoding='utf-8') as handle:
            return found, json.load(handle), get

    def test_find_triage_reports_announces_nothing_on_the_first_run(self):
        # Waking up to a notification for every report ever posted would train you to ignore
        # them. The first run only records where to start looking -- which is also what makes
        # the rename of the state file safe.
        found, state, get = self._triage_reports([self._report_comment()])

        self.assertEqual(found, [])
        self.assertTrue(state['since'])
        get.assert_not_called()

    def test_find_triage_reports_reports_a_new_report(self):
        found, state, _ = self._triage_reports(
            [self._report_comment()],
            state={'since': '2026-08-11T09:00:00Z', 'seen': []},
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['issue'], 12)
        self.assertIn('issuecomment-900', found[0]['url'])
        self.assertIn(900, state['seen'])

    def test_find_triage_reports_ignores_a_quoted_heading_from_a_human(self):
        # Anyone who can comment could otherwise announce a diagnosis nobody produced.
        found, _, _ = self._triage_reports(
            [self._report_comment(user={'login': 'freender'})],
            state={'since': '2026-08-11T09:00:00Z', 'seen': []},
        )

        self.assertEqual(found, [])

    def test_find_triage_reports_does_not_repeat_an_announced_comment(self):
        found, _, _ = self._triage_reports(
            [self._report_comment()],
            state={'since': '2026-08-11T09:00:00Z', 'seen': [900]},
        )

        self.assertEqual(found, [])

    def test_find_triage_reports_ignores_other_flow_comments(self):
        # The fix flow comments on the same issues as the same author; only a report counts.
        found, state, _ = self._triage_reports(
            [self._report_comment(id=901, body='## Fixed\n\n**What I ran** ...')],
            state={'since': '2026-08-11T09:00:00Z', 'seen': []},
        )

        self.assertEqual(found, [])
        # The window still advances, or a non-matching comment would be re-read forever.
        self.assertEqual(state['since'], '2026-08-11T10:00:00Z')

    def test_find_open_incident_does_not_search_without_a_fingerprint(self):
        incidents = importlib.import_module('modules.incidents')

        with mock.patch.object(incidents.requests, 'get') as get:
            self.assertIsNone(incidents.find_open_incident(None))

        get.assert_not_called()

    def test_create_incident_warns_when_triage_trigger_fails(self):
        issue_response = mock.Mock(status_code=201)
        issue_response.json.return_value = {
            'number': 7,
            'title': 'plex is down',
            'html_url': 'https://github.com/freender/homelab-ops/issues/7',
        }
        comment_response = mock.Mock(status_code=403)
        incidents = importlib.import_module('modules.incidents')

        with mock.patch.object(
            incidents.requests, 'post', side_effect=[issue_response, comment_response]
        ):
            incident, error = self.modules.create_incident('plex is down on tower')

        self.assertIsNone(error)
        self.assertIn('403', incident['warning'])

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

    def test_authorization_accepts_a_seerr_telegram_chat_id(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '123456789'},
            {'telegramChatId': '987654321'},
        ]
        with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
            self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_auth_chat_id(987654321))

    def test_ownership_uses_the_seerr_owner_telegram_chat_id(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '123456789'},
            {'telegramChatId': '987654321'},
        ]
        with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
            self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_owner_chat_id(123456789))
        self.assertFalse(self.modules.is_owner_chat_id(987654321))

    def test_warm_seerr_access_cache_can_force_owner_to_authorized_only(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '123456789'},
            {'telegramChatId': '987654321'},
        ]
        with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_USER_ID', 123456789, create=True):
            with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_MODE', 'authorized', create=True):
                with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
                    self.modules.warm_seerr_access_cache()

        self.assertTrue(self.modules.is_auth_chat_id(123456789))
        self.assertFalse(self.modules.is_owner_chat_id(123456789))

    def test_warm_seerr_access_cache_can_force_user_to_unauthorized(self):
        payload = {'results': [{'id': 1}, {'id': 3}], 'pageInfo': {'results': 2}}
        settings = [
            {'telegramChatId': '123456789'},
            {'telegramChatId': '987654321'},
        ]
        with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_USER_ID', 987654321, create=True):
            with mock.patch.object(self.cfg, 'SEERR_ACCESS_TEST_MODE', 'unauthorized', create=True):
                with mock.patch.object(self.modules, 'request_json', side_effect=[payload] + settings):
                    self.modules.warm_seerr_access_cache()

        self.assertFalse(self.modules.is_auth_chat_id(987654321))
        self.assertFalse(self.modules.is_owner_chat_id(987654321))

    def test_warm_seerr_access_cache_uses_empty_access_when_seerr_fails(self):
        with mock.patch.object(self.modules, 'request_json', side_effect=RuntimeError('boom')):
            cache = self.modules.warm_seerr_access_cache()

        self.assertTrue(cache['loaded'])
        self.assertFalse(self.modules.is_auth_chat_id(2))

    def test_build_alert_incident_text_without_annotations(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {
            'labels': {'alertname': 'ContainerRestarting', 'name': 'plex', 'host': 'tower'},
            'annotations': {},
            'startsAt': '2026-08-09T05:06:00.000Z',
            'status': {'silencedBy': ['abc']},
        }

        text = alertmanager.build_alert_incident_text(alert)

        self.assertEqual(text.splitlines()[0], 'ContainerRestarting on plex @ tower')
        self.assertIn('- host: tower', text)
        self.assertIn('- firing since: 2026-08-09T05:06:00.000Z', text)
        self.assertIn('- suppressed: silenced', text)

    def test_alert_button_label_truncates_long_names(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'A' * 80, 'host': 'ace', 'severity': 'critical'}}

        label = alertmanager.alert_button_label(alert)

        self.assertTrue(label.startswith('🔴 ace: '))
        self.assertTrue(label.endswith('…'))
        self.assertLessEqual(len(label), 48)

    def test_get_alert_choices_returns_none_when_unreachable(self):
        alertmanager = importlib.import_module('modules.alertmanager')

        with mock.patch.object(alertmanager, 'get_active_alerts', return_value=None):
            self.assertIsNone(alertmanager.get_alert_choices())

    def test_get_alert_choices_sorts_critical_first(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alerts = [
            {'labels': {'alertname': 'B', 'host': 'ace', 'severity': 'warning'}},
            {'labels': {'alertname': 'A', 'host': 'tower', 'severity': 'critical'}},
        ]

        with mock.patch.object(alertmanager, 'get_active_alerts', return_value=alerts):
            choices = alertmanager.get_alert_choices()

        self.assertEqual([a['labels']['alertname'] for a in choices], ['A', 'B'])

    def test_get_alert_choices_keeps_alerts_no_single_action_applies_to(self):
        """One list for the whole section; eligibility is decided per alert, not by filtering."""
        alertmanager = importlib.import_module('modules.alertmanager')
        metric_alert = {'labels': {'alertname': 'ContainerMissing', 'host': 'tower', 'severity': 'critical'}}
        event_alert = {'labels': {'alertname': 'ProxmoxNotification', 'host': 'osiris',
                                  'severity': 'critical', 'source': 'pve'}}

        with mock.patch.object(alertmanager, 'get_active_alerts', return_value=[metric_alert, event_alert]):
            choices = alertmanager.get_alert_choices()

        self.assertEqual(len(choices), 2)
        self.assertFalse(alertmanager.is_resolvable(metric_alert))
        self.assertTrue(alertmanager.is_resolvable(event_alert))

    def test_silence_alert_matches_that_alert_exactly(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'ContainerMissing', 'host': 'tower', 'severity': 'critical'}}

        with mock.patch.object(alertmanager, 'create_silence', return_value='sil-1') as create_silence:
            self.assertEqual(alertmanager.silence_alert(alert, timedelta(hours=2)), 'sil-1')

        matchers = create_silence.call_args.kwargs['matchers']
        self.assertEqual(
            matchers,
            [
                {'name': 'alertname', 'value': 'ContainerMissing', 'isRegex': False, 'isEqual': True},
                {'name': 'host', 'value': 'tower', 'isRegex': False, 'isEqual': True},
                {'name': 'severity', 'value': 'critical', 'isRegex': False, 'isEqual': True},
            ],
        )
        self.assertEqual(create_silence.call_args.kwargs['comment'], alertmanager.ALERT_SILENCE_COMMENT)

    def test_silence_alert_refuses_an_unlabelled_alert(self):
        alertmanager = importlib.import_module('modules.alertmanager')

        with mock.patch.object(alertmanager, 'create_silence') as create_silence:
            self.assertIsNone(alertmanager.silence_alert({'labels': {}}, timedelta(hours=2)))

        create_silence.assert_not_called()

    def test_unsilence_ignores_the_blanket_maintenance_silence(self):
        """Unsilencing one alert must not lift the window muting all of them."""
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['mw-1', 'alert-1']}}
        silences = {
            'mw-1': {'comment': 'mwbot-alertmanager-maintenance'},
            'alert-1': {'comment': alertmanager.ALERT_SILENCE_COMMENT},
        }

        with mock.patch.object(alertmanager, 'get_silence', side_effect=lambda sid: silences[sid]), \
             mock.patch.object(alertmanager, 'expire_silence', return_value=True) as expire_silence:
            self.assertTrue(alertmanager.unsilence_alert(alert))

        expire_silence.assert_called_once_with('alert-1')

    def test_silence_index_resolves_a_whole_list_in_one_call(self):
        """Rendering the list must not cost a GET per silenced alert."""
        alertmanager = importlib.import_module('modules.alertmanager')
        payload = [{'id': 'a', 'comment': alertmanager.ALERT_SILENCE_COMMENT},
                   {'id': 'b', 'comment': 'mwbot-alertmanager-maintenance'}]

        with mock.patch.object(alertmanager, 'request_json', return_value=payload) as request_json:
            index = alertmanager.silence_index()

        request_json.assert_called_once()
        self.assertTrue(request_json.call_args[0][1].endswith('/api/v2/silences'))

        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['a', 'b']}}
        with mock.patch.object(alertmanager, 'get_silence') as get_silence:
            self.assertEqual(alertmanager.alert_silence_ids(alert, index=index), ['a'])
        get_silence.assert_not_called()

    def test_alert_silenced_until_reports_the_latest_of_our_silences(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        index = {
            'a': {'comment': alertmanager.ALERT_SILENCE_COMMENT, 'endsAt': '2026-08-20T10:00:00.000Z'},
            'b': {'comment': alertmanager.ALERT_SILENCE_COMMENT, 'endsAt': '2026-08-23T10:00:00.000Z'},
            'mw': {'comment': 'mwbot-alertmanager-maintenance', 'endsAt': '2026-09-01T10:00:00.000Z'},
        }
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['a', 'b', 'mw']}}

        ends_at = alertmanager.alert_silenced_until(alert, index=index)

        self.assertEqual(ends_at, datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc))

    def test_alert_silenced_until_is_none_without_one_of_ours(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        index = {'mw': {'comment': 'mwbot-alertmanager-maintenance',
                        'endsAt': '2026-09-01T10:00:00.000Z'}}
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['mw']}}

        self.assertIsNone(alertmanager.alert_silenced_until(alert, index=index))

    def test_alert_silenced_until_survives_an_unparseable_timestamp(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        index = {'a': {'comment': alertmanager.ALERT_SILENCE_COMMENT, 'endsAt': 'not-a-time'}}
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['a']}}

        self.assertIsNone(alertmanager.alert_silenced_until(alert, index=index))

    def test_get_silences_is_empty_when_alertmanager_is_unreachable(self):
        alertmanager = importlib.import_module('modules.alertmanager')

        with mock.patch.object(alertmanager, 'request_json', side_effect=RuntimeError('boom')):
            self.assertEqual(alertmanager.get_silences(), [])

    def test_unsilence_reports_false_when_nothing_of_ours_applies(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['mw-1']}}

        with mock.patch.object(alertmanager, 'get_silence',
                               return_value={'comment': 'mwbot-alertmanager-maintenance'}), \
             mock.patch.object(alertmanager, 'expire_silence') as expire_silence:
            self.assertFalse(alertmanager.unsilence_alert(alert))

        expire_silence.assert_not_called()

    def test_unsilence_expires_every_silence_even_after_one_fails(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'X'}, 'status': {'silencedBy': ['a', 'b']}}

        with mock.patch.object(alertmanager, 'get_silence',
                               return_value={'comment': alertmanager.ALERT_SILENCE_COMMENT}), \
             mock.patch.object(alertmanager, 'expire_silence', side_effect=[False, True]) as expire_silence:
            self.assertFalse(alertmanager.unsilence_alert(alert))

        self.assertEqual([call.args[0] for call in expire_silence.call_args_list], ['a', 'b'])

    def test_resolve_alert_posts_same_labels_with_past_end(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {
            'labels': {'alertname': 'ProxmoxNotification', 'source': 'pve', 'host': 'osiris'},
            'annotations': {'summary': 'backup failed'},
            'startsAt': '2026-08-09T05:06:00.000Z',
        }

        with mock.patch.object(alertmanager, 'request_json', return_value=None) as request_json:
            self.assertTrue(alertmanager.resolve_alert(alert))

        method, url = request_json.call_args[0]
        payload = request_json.call_args[1]['payload']
        self.assertEqual(method, 'POST')
        self.assertTrue(url.endswith('/api/v2/alerts'))
        # Alertmanager matches on the exact label set; a changed label creates a
        # second alert instead of clearing the original.
        self.assertEqual(payload[0]['labels'], alert['labels'])
        self.assertEqual(payload[0]['startsAt'], alert['startsAt'])
        self.assertIn('endsAt', payload[0])

    def test_resolve_alert_reports_failure(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alert = {'labels': {'alertname': 'ProxmoxNotification', 'source': 'pve'}}

        with mock.patch.object(alertmanager, 'request_json', side_effect=RuntimeError('boom')):
            self.assertFalse(alertmanager.resolve_alert(alert))

    def test_resolve_alert_refuses_unlabelled_alert(self):
        alertmanager = importlib.import_module('modules.alertmanager')

        with mock.patch.object(alertmanager, 'request_json') as request_json:
            self.assertFalse(alertmanager.resolve_alert({'labels': {}}))

        request_json.assert_not_called()

    def test_start_alertmanager_mw_creates_independent_state(self):
        with mock.patch('modules.alertmanager.create_silence', return_value='silence-1') as create_silence:
            result = self.modules.start_alertmanager_mw(timedelta(hours=2))

        self.assertIn('Alertmanager maintenance started.', result)
        create_silence.assert_called_once_with(
            timedelta(hours=2),
            comment='mwbot-alertmanager-maintenance',
        )
        self.assertEqual(self.modules.load_alertmanager_mw_state()['silence_id'], 'silence-1')

    def test_start_alertmanager_mw_preserves_unverified_existing_state(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.get_silence', return_value=None), \
             mock.patch('modules.alertmanager.create_silence') as create_silence:
            result = self.modules.start_alertmanager_mw()

        self.assertEqual(result, 'Unable to verify the existing Alertmanager maintenance window.')
        create_silence.assert_not_called()
        self.assertEqual(self.modules.load_alertmanager_mw_state()['silence_id'], 'silence-1')

    def test_start_alertmanager_mw_does_not_duplicate_active_silence(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.get_silence', return_value={'status': {'state': 'active'}}), \
             mock.patch('modules.alertmanager.create_silence') as create_silence:
            result = self.modules.start_alertmanager_mw()

        self.assertIn('already active', result)
        create_silence.assert_not_called()

    def test_start_alertmanager_mw_rolls_back_when_state_save_fails(self):
        with mock.patch('modules.alertmanager.create_silence', return_value='silence-1'), \
             mock.patch('modules.alertmanager.expire_silence', return_value=True) as expire_silence, \
             mock.patch.object(self.maintenance, 'save_alertmanager_mw_state', side_effect=OSError('disk full')):
            result = self.modules.start_alertmanager_mw()

        self.assertEqual(
            result,
            'Unable to save Alertmanager maintenance state. The silence was rolled back.',
        )
        expire_silence.assert_called_once_with('silence-1')

    def test_alertmanager_window_text_verifies_active_silence(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.get_silence', return_value={'status': {'state': 'active'}}):
            result = self.modules.get_alertmanager_window_text()

        self.assertIn('Maintenance active', result)
        self.assertIn('left', result)

    def test_alertmanager_window_text_is_empty_when_no_window_is_active(self):
        """Empty means inactive: it is what the alerts menu keys its button off."""
        self.assertEqual(self.modules.get_alertmanager_window_text(), '')

    def test_alertmanager_window_text_clears_state_for_an_expired_silence(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.get_silence', return_value={'status': {'state': 'expired'}}):
            self.assertEqual(self.modules.get_alertmanager_window_text(), '')

        self.assertIsNone(self.modules.load_alertmanager_mw_state())

    def test_alertmanager_window_text_keeps_an_unverifiable_window(self):
        """Dropping it would leave Alertmanager muted with no button left to unmute it."""
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.get_silence', return_value=None):
            result = self.modules.get_alertmanager_window_text()

        self.assertIn('unverified', result)
        self.assertIsNotNone(self.modules.load_alertmanager_mw_state())

    def test_get_active_alerts_includes_suppressed_alerts(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        payload = [{'labels': {'alertname': 'CriticalContainerMissing'}}]

        with mock.patch.object(alertmanager, 'request_json', return_value=payload) as request_json:
            result = alertmanager.get_active_alerts()

        self.assertEqual(result, payload)
        request_json.assert_called_once_with(
            'GET',
            'http://alertmanager.local:9093/api/v2/alerts',
            params={
                'active': 'true',
                'silenced': 'true',
                'inhibited': 'true',
                'unprocessed': 'true',
            },
            timeout=10,
        )

    def test_get_active_alerts_drops_always_firing_alertnames(self):
        """Watchdog is a dead-man's switch, not a condition: it must never be reported.

        Filtering at the fetch is what keeps the alerts list from offering to file a
        GitHub issue against a healthy monitoring pipeline.
        """
        alertmanager = importlib.import_module('modules.alertmanager')
        real = {'labels': {'alertname': 'ZfsPoolUnhealthy', 'host': 'ace', 'severity': 'critical'}}
        payload = [{'labels': {'alertname': 'Watchdog', 'severity': 'none'}}, real]

        with mock.patch.object(alertmanager, 'request_json', return_value=payload):
            self.assertEqual(alertmanager.get_active_alerts(), [real])
            choices = alertmanager.get_alert_choices()

        self.assertEqual(choices, [real])
        self.assertEqual(alertmanager.format_alert_summary(choices), '1 active alert')

    def test_get_active_alerts_reports_nothing_when_only_excluded_fire(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        payload = [{'labels': {'alertname': 'Watchdog', 'severity': 'none'}}]

        with mock.patch.object(alertmanager, 'request_json', return_value=payload):
            self.assertEqual(alertmanager.get_active_alerts(), [])
            summary = alertmanager.format_alert_summary(alertmanager.get_alert_choices())
        self.assertEqual(summary, 'All clear. Nothing is firing.')

    def test_get_active_alerts_unreachable_is_not_an_empty_list(self):
        """None and [] must stay distinguishable, or an outage reads as 'all clear'."""
        alertmanager = importlib.import_module('modules.alertmanager')

        with mock.patch.object(alertmanager, 'request_json', side_effect=RuntimeError('boom')):
            self.assertIsNone(alertmanager.get_active_alerts())

    def test_alert_summary_reports_all_clear(self):
        alertmanager = importlib.import_module('modules.alertmanager')

        self.assertEqual(alertmanager.format_alert_summary([]), 'All clear. Nothing is firing.')

    def test_alert_summary_distinguishes_unreachable_from_all_clear(self):
        """None and [] must not read the same, or an outage looks like a healthy homelab."""
        alertmanager = importlib.import_module('modules.alertmanager')

        self.assertIn('unavailable', alertmanager.format_alert_summary(None))

    def test_alert_summary_counts_suppressed_alerts(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alerts = [
            {'labels': {'alertname': 'A', 'severity': 'warning'}, 'status': {}},
            {'labels': {'alertname': 'B', 'severity': 'critical'},
             'status': {'silencedBy': ['silence-1']}},
            {'labels': {'alertname': 'C', 'severity': 'info'},
             'status': {'inhibitedBy': ['alert-1']}},
        ]

        self.assertEqual(
            alertmanager.format_alert_summary(alerts),
            '3 active alerts · 2 suppressed',
        )

    def test_alert_summary_does_not_pluralise_a_single_alert(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alerts = [{'labels': {'alertname': 'A', 'severity': 'warning'}, 'status': {}}]

        self.assertEqual(alertmanager.format_alert_summary(alerts), '1 active alert')

    def test_alert_choices_are_capped(self):
        alertmanager = importlib.import_module('modules.alertmanager')
        alerts = [
            {'labels': {'alertname': f'Alert{index}', 'name': f'app-{index}', 'severity': 'warning'}}
            for index in range(3)
        ]

        with mock.patch.object(alertmanager, 'get_active_alerts', return_value=alerts):
            self.assertEqual(len(alertmanager.get_alert_choices(limit=2)), 2)

    def test_stop_alertmanager_mw_expires_silence_and_clears_state(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.expire_silence', return_value=True) as expire_silence:
            result = self.modules.stop_alertmanager_mw()

        self.assertEqual(result, 'Alertmanager maintenance completed.')
        expire_silence.assert_called_once_with('silence-1')
        self.assertIsNone(self.modules.load_alertmanager_mw_state())

    def test_stop_alertmanager_mw_keeps_state_when_expiration_fails(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.expire_silence', return_value=False):
            result = self.modules.stop_alertmanager_mw()

        self.assertEqual(result, 'Unable to stop Alertmanager maintenance.')
        self.assertIsNotNone(self.modules.load_alertmanager_mw_state())

    def test_stop_alertmanager_mw_reports_local_cleanup_failure(self):
        self.maintenance.save_alertmanager_mw_state({
            'silence_id': 'silence-1',
            'expires_at': (datetime.now(ZoneInfo(self.cfg.TZ)) + timedelta(hours=1)).isoformat(),
            'duration': '1h',
        })

        with mock.patch('modules.alertmanager.expire_silence', return_value=True), \
             mock.patch.object(self.maintenance, 'clear_alertmanager_mw_state', return_value=False):
            result = self.modules.stop_alertmanager_mw()

        self.assertEqual(result, 'Alertmanager maintenance completed, but local state cleanup failed.')

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

    def test_get_firewall_status_text_lists_temporary_networks(self):
        self.firewall._save_as_organization('7922', 'Comcast Cable')
        with mock.patch.object(self.firewall, 'get_rule_status', return_value=(True, None)):
            with mock.patch.object(
                self.firewall,
                'get_asns_from_firewall_rule',
                return_value=(['1234', '7922'], None),
            ):
                status = self.modules.get_firewall_status_text()

        self.assertEqual(status, 'Plex access is enabled. Temporary networks: Comcast Cable (AS7922).')

    def test_build_rule_payload_uses_asn_only(self):
        payload = self.firewall._build_rule_payload(['1234', '7922'], enabled=True)

        self.assertEqual(
            payload['expression'],
            '(ip.geoip.asnum in {1234 7922} and http.host wildcard "example.com")',
        )
        self.assertNotIn('ip.src', payload['expression'])
        self.assertTrue(payload['enabled'])

    def test_get_asns_from_legacy_firewall_rule_ignores_ip_clause(self):
        rule = {
            'expression': '((ip.src in {192.0.2.1 2001:db8::1} or '
                          'ip.geoip.asnum in {1234 7922}) and http.host wildcard "example.com")',
        }
        with mock.patch.object(self.firewall, '_get_waf_rule', return_value=(rule, None)):
            asns, error = self.modules.get_asns_from_firewall_rule()

        self.assertEqual(asns, ['1234', '7922'])
        self.assertIsNone(error)

    def test_grant_network_access_adds_asn_and_canonicalizes_rule(self):
        update_lock = mock.MagicMock()
        with mock.patch.object(
            self.firewall,
            'get_asns_from_firewall_rule',
            return_value=(['1234'], None),
        ), mock.patch.object(
            self.firewall,
            '_update_firewall_rule',
            return_value=(True, None),
        ) as update_rule, mock.patch.object(self.firewall, '_WAF_UPDATE_LOCK', update_lock):
            success, result = self.modules.grant_network_access('7922', 'Comcast Cable')

        self.assertTrue(success)
        self.assertEqual(result, 'Network access granted.')
        self.assertEqual(self.firewall._load_as_organizations(), {'7922': 'Comcast Cable'})
        expression = update_rule.call_args.args[0]['expression']
        self.assertIn('ip.geoip.asnum in {1234 7922}', expression)
        self.assertNotIn('ip.src', expression)
        update_lock.__enter__.assert_called_once_with()
        update_lock.__exit__.assert_called_once()

    def test_create_network_check_validates_worker_response(self):
        payload = {
            'id': 'a' * 43,
            'check_url': f'https://access-check.example.com/check/{"a" * 43}',
        }
        with mock.patch.object(self.network_check, 'request_json', return_value=payload):
            session, error = self.modules.create_network_check()

        self.assertEqual(session, payload)
        self.assertIsNone(error)

    def test_create_network_check_rejects_cross_origin_check_url(self):
        payload = {
            'id': 'a' * 43,
            'check_url': f'https://attacker.example/check/{"a" * 43}',
        }
        with mock.patch.object(self.network_check, 'request_json', return_value=payload):
            session, error = self.modules.create_network_check()

        self.assertIsNone(session)
        self.assertIn('invalid session', error)

    def test_network_check_rejects_insecure_api_url(self):
        with mock.patch.object(self.cfg, 'ACCESS_CHECK_API_URL', 'http://access-check.example.com'):
            self.assertFalse(self.modules.network_check_is_configured())

    def test_get_network_check_validates_complete_network(self):
        payload = {
            'status': 'complete',
            'asn': 7922,
            'as_organization': 'Comcast Cable',
        }
        with mock.patch.object(self.network_check, 'request_json', return_value=payload):
            detected, error = self.modules.get_network_check('session-id')

        self.assertEqual(
            detected,
            {
                'status': 'complete',
                'asn': '7922',
                'as_organization': 'Comcast Cable',
            },
        )
        self.assertIsNone(error)

    def test_get_network_check_ignores_invalid_organization(self):
        payload = {'status': 'complete', 'asn': 7922, 'as_organization': 'x' * 121}
        with mock.patch.object(self.network_check, 'request_json', return_value=payload):
            detected, error = self.modules.get_network_check('session-id')

        self.assertEqual(detected, {'status': 'complete', 'asn': '7922'})
        self.assertIsNone(error)

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
