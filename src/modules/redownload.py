import logging
import re

import requests

import cfg
from modules.common import build_api_headers, extract_records, normalize_base_url, request_json


SEERR_ISSUE_URL_PATTERN = re.compile(r'(?:https?://)?[^\s]+/issues/(\d+)(?:[/?#].*)?$', re.IGNORECASE)
SEERR_MEDIA_URL_PATTERN = re.compile(r'(?:https?://)?[^\s]+/(?P<media_type>movie|tv|series)/(?:[^/?#]+/)?(?P<tmdb_id>\d+)(?:[/?#].*)?$', re.IGNORECASE)


def parse_seerr_issue_url(text):
    if not text:
        return None, 'Please send a Seerr issue URL.'

    match = SEERR_ISSUE_URL_PATTERN.match(text.strip())
    if not match:
        return None, 'Invalid Seerr issue URL. Use a URL like https://seerr.example.com/issues/29'
    return int(match.group(1)), None


def parse_seerr_reference(text):
    stripped_text = (text or '').strip()
    issue_id, error = parse_seerr_issue_url(stripped_text)
    if issue_id is not None:
        return {'reference_type': 'issue', 'issue_id': issue_id}, None

    match = SEERR_MEDIA_URL_PATTERN.match(stripped_text)
    if match:
        media_type = match.group('media_type').lower()
        if media_type == 'series':
            media_type = 'tv'
        return {
            'reference_type': 'media',
            'media_type': media_type,
            'tmdb_id': int(match.group('tmdb_id')),
        }, None

    if error is None:
        error = 'Invalid Seerr URL.'
    return None, error.replace('issue URL', 'issue, movie, or series URL')


def get_seerr_issue(issue_id):
    url = f"{normalize_base_url(cfg.SEERR_BASE_URL)}/api/v1/issue/{issue_id}"
    try:
        return request_json('GET', url, headers=build_api_headers(cfg.SEERR_API_KEY)), None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        if status_code == 404:
            return None, 'Seerr issue was not found.'
        logging.error('Unable to fetch Seerr issue %s: %s', issue_id, exc)
        return None, f'Seerr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Seerr issue %s: %s', issue_id, exc)
        return None, 'Unable to reach Seerr.'


def get_seerr_media_details(media_type, tmdb_id):
    endpoint = 'movie' if media_type == 'movie' else 'tv'
    url = f"{normalize_base_url(cfg.SEERR_BASE_URL)}/api/v1/{endpoint}/{tmdb_id}"
    try:
        return request_json('GET', url, headers=build_api_headers(cfg.SEERR_API_KEY)), None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        if status_code == 404:
            return None, f'Seerr {endpoint} page was not found.'
        logging.error('Unable to fetch Seerr %s %s: %s', endpoint, tmdb_id, exc)
        return None, f'Seerr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Seerr %s %s: %s', endpoint, tmdb_id, exc)
        return None, 'Unable to reach Seerr.'


def get_all_seerr_issue_ids():
    issue_ids = []
    page = 1
    take = 100

    while True:
        url = f"{normalize_base_url(cfg.SEERR_BASE_URL)}/api/v1/issue"
        payload = request_json(
            'GET',
            url,
            headers=build_api_headers(cfg.SEERR_API_KEY),
            params={'filter': 'all', 'take': take, 'page': page},
        ) or {}
        results = payload.get('results', [])
        issue_ids.extend(result.get('id') for result in results if result.get('id') is not None)

        page_info = payload.get('pageInfo') or {}
        total_pages = page_info.get('pages') or 0
        if page >= total_pages or not results:
            return issue_ids
        page += 1


def issue_sort_key(issue):
    return (
        bool(issue.get('problemSeason') and issue.get('problemEpisode')),
        issue.get('updatedAt') or '',
        issue.get('createdAt') or '',
        issue.get('id') or 0,
    )


def find_seerr_issue_for_media(media_type, tmdb_id):
    media_details, error = get_seerr_media_details(media_type, tmdb_id)
    if media_details is None:
        return None, None, error

    media_info = media_details.get('mediaInfo') or {}
    media_id = media_info.get('id')
    if media_id is None:
        noun = 'movie' if media_type == 'movie' else 'series'
        return None, media_details, f'No Seerr issue was found for this {noun} URL.'

    try:
        issue_ids = get_all_seerr_issue_ids()
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to list Seerr issues for media %s %s: %s', media_type, tmdb_id, exc)
        return None, media_details, f'Seerr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to list Seerr issues for media %s %s: %s', media_type, tmdb_id, exc)
        return None, media_details, 'Unable to reach Seerr.'

    matching_issues = []
    for issue_id in issue_ids:
        issue, issue_error = get_seerr_issue(issue_id)
        if issue is None:
            logging.warning('Skipping Seerr issue %s during media lookup: %s', issue_id, issue_error)
            continue
        issue_media = issue.get('media') or {}
        if issue_media.get('id') == media_id or issue_media.get('tmdbId') == tmdb_id:
            matching_issues.append(issue)

    if not matching_issues:
        noun = 'movie' if media_type == 'movie' else 'series'
        return None, media_details, f'No Seerr issue was found for this {noun} URL.'

    matching_issues.sort(key=issue_sort_key, reverse=True)
    return matching_issues[0], media_details, None


def build_target_label(issue, media_details, target):
    if issue.get('subject'):
        return issue['subject']

    if target['media_type'] == 'movie':
        return media_details.get('title') or target.get('label')

    show_name = media_details.get('name') or target.get('label')
    return f"{show_name} S{target['season_number']:02d}E{target['episode_number']:02d}"


def get_issue_target(issue):
    media = issue.get('media') or {}
    media_type = media.get('mediaType')
    service_id = media.get('externalServiceId')
    service_id_4k = media.get('externalServiceId4k')
    service_url = (media.get('serviceUrl') or '').lower()
    use_four_k = bool(service_id_4k) and (service_id is None or '4k' in service_url)

    if media_type == 'movie':
        selected_movie_id = service_id_4k if use_four_k else service_id
        if selected_movie_id is None:
            return None, 'Seerr issue is missing the Radarr movie mapping.'
        return {
            'media_type': 'movie',
            'movie_id': selected_movie_id,
            'is_4k': use_four_k,
            'label': issue.get('subject') or f'Movie #{selected_movie_id}',
        }, None

    if media_type == 'tv':
        season_number = issue.get('problemSeason')
        episode_number = issue.get('problemEpisode')
        selected_series_id = service_id_4k if use_four_k else service_id
        if selected_series_id is None:
            return None, 'Seerr issue is missing the Sonarr series mapping.'
        if not season_number or not episode_number:
            return None, 'Seerr issue is not tied to a specific episode.'
        return {
            'media_type': 'episode',
            'series_id': selected_series_id,
            'season_number': season_number,
            'episode_number': episode_number,
            'is_4k': use_four_k,
            'label': issue.get('subject') or f'Series #{selected_series_id} S{season_number:02d}E{episode_number:02d}',
        }, None

    return None, 'Unsupported Seerr media type.'


def get_episode(series_id, season_number, episode_number, base_url=None, api_key=None):
    resolved_base_url = base_url or cfg.SONARR_BASE_URL
    resolved_api_key = api_key or cfg.SONARR_API_KEY
    url = f"{normalize_base_url(resolved_base_url)}/api/v3/episode"
    try:
        episodes = extract_records(request_json(
            'GET',
            url,
            headers=build_api_headers(resolved_api_key),
            params={'seriesId': series_id, 'seasonNumber': season_number},
        ))
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Sonarr episodes for series %s: %s', series_id, exc)
        return None, f'Sonarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Sonarr episodes for series %s: %s', series_id, exc)
        return None, 'Unable to reach Sonarr.'

    for episode in episodes:
        if episode.get('episodeNumber') == episode_number:
            return episode, None
    return None, 'Episode was not found in Sonarr.'


def find_queue_item(queue_items, expected_key, expected_value):
    for item in queue_items:
        if item.get(expected_key) == expected_value:
            return item
        episode_ids = item.get('episodeIds') or []
        if expected_key == 'episodeId' and expected_value in episode_ids:
            return item
    return None


def get_arr_service(target):
    if target['media_type'] == 'movie':
        return (
            'Radarr4k' if target.get('is_4k') else 'Radarr',
            cfg.RADARR4K_BASE_URL if target.get('is_4k') else cfg.RADARR_BASE_URL,
            cfg.RADARR4K_API_KEY if target.get('is_4k') else cfg.RADARR_API_KEY,
        )
    return (
        'Sonarr4k' if target.get('is_4k') else 'Sonarr',
        cfg.SONARR4K_BASE_URL if target.get('is_4k') else cfg.SONARR_BASE_URL,
        cfg.SONARR4K_API_KEY if target.get('is_4k') else cfg.SONARR_API_KEY,
    )


def select_failed_history_record(records, expected_key, expected_value):
    prioritized_events = ('grabbed', 'downloadFolderImported')
    for event_type in prioritized_events:
        for record in records:
            if record.get(expected_key) == expected_value and record.get('eventType') == event_type:
                return record

    for record in records:
        if record.get(expected_key) == expected_value:
            return record
    return None


def find_imported_history_record(records, expected_key, expected_value):
    for record in records:
        if record.get(expected_key) == expected_value and record.get('eventType') == 'downloadFolderImported':
            return record
    return None


def find_grabbed_record_for_import(records, imported_record, expected_key, expected_value):
    download_id = imported_record.get('downloadId')
    source_title = imported_record.get('sourceTitle')

    if download_id:
        for record in records:
            if (
                record.get(expected_key) == expected_value
                and record.get('eventType') == 'grabbed'
                and record.get('downloadId') == download_id
            ):
                return record

    if source_title:
        for record in records:
            if (
                record.get(expected_key) == expected_value
                and record.get('eventType') == 'grabbed'
                and record.get('sourceTitle') == source_title
            ):
                return record

    return None


def delete_queue_item(base_url, api_key, queue_id):
    url = f"{normalize_base_url(base_url)}/api/v3/queue/{queue_id}"
    try:
        request_json(
            'DELETE',
            url,
            headers=build_api_headers(api_key),
            params={'removeFromClient': 'true', 'blocklist': 'true', 'skipRedownload': 'false'},
        )
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to delete queue item %s: %s', queue_id, exc)
        return False, f'Queue removal failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to delete queue item %s: %s', queue_id, exc)
        return False, 'Unable to reach arr service queue API.'


def mark_history_failed(base_url, api_key, history_id):
    url = f"{normalize_base_url(base_url)}/api/v3/history/failed/{history_id}"
    try:
        request_json('POST', url, headers=build_api_headers(api_key))
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to mark history %s failed: %s', history_id, exc)
        return False, f'History blacklist failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to mark history %s failed: %s', history_id, exc)
        return False, 'Unable to reach arr service history API.'


def get_movie(base_url, api_key, movie_id):
    url = f"{normalize_base_url(base_url)}/api/v3/movie/{movie_id}"
    try:
        return request_json('GET', url, headers=build_api_headers(api_key)), None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Radarr movie %s: %s', movie_id, exc)
        return None, f'Radarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Radarr movie %s: %s', movie_id, exc)
        return None, 'Unable to reach Radarr.'


def delete_movie_file(base_url, api_key, file_id):
    url = f"{normalize_base_url(base_url)}/api/v3/moviefile/{file_id}"
    try:
        request_json('DELETE', url, headers=build_api_headers(api_key))
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to delete Radarr movie file %s: %s', file_id, exc)
        return False, f'Movie file delete failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to delete Radarr movie file %s: %s', file_id, exc)
        return False, 'Unable to reach Radarr movie file API.'


def delete_episode_file(base_url, api_key, file_id):
    url = f"{normalize_base_url(base_url)}/api/v3/episodefile/{file_id}"
    try:
        request_json('DELETE', url, headers=build_api_headers(api_key))
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to delete Sonarr episode file %s: %s', file_id, exc)
        return False, f'Episode file delete failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to delete Sonarr episode file %s: %s', file_id, exc)
        return False, 'Unable to reach Sonarr episode file API.'


def trigger_movie_search(base_url, api_key, movie_id):
    url = f"{normalize_base_url(base_url)}/api/v3/command"
    payload = {'name': 'MoviesSearch', 'movieIds': [movie_id]}
    try:
        request_json('POST', url, headers=build_api_headers(api_key), payload=payload)
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to trigger Radarr search for movie %s: %s', movie_id, exc)
        return False, f'Movie search failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to trigger Radarr search for movie %s: %s', movie_id, exc)
        return False, 'Unable to reach Radarr command API.'


def trigger_episode_search(base_url, api_key, episode_id):
    url = f"{normalize_base_url(base_url)}/api/v3/command"
    payload = {'name': 'EpisodeSearch', 'episodeIds': [episode_id]}
    try:
        request_json('POST', url, headers=build_api_headers(api_key), payload=payload)
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to trigger Sonarr search for episode %s: %s', episode_id, exc)
        return False, f'Episode search failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to trigger Sonarr search for episode %s: %s', episode_id, exc)
        return False, 'Unable to reach Sonarr command API.'


def describe_file(file_info):
    if not file_info:
        return 'Not currently downloaded'
    return file_info.get('path') or file_info.get('relativePath') or f"File #{file_info.get('id')}"


def resolve_movie_replacement(target):
    service_name, base_url, api_key = get_arr_service(target)
    movie, error = get_movie(base_url, api_key, target['movie_id'])
    if movie is None:
        return None, error

    movie_file = movie.get('movieFile') or None
    target.update({
        'service': service_name,
        'file_id': movie_file.get('id') if movie_file else None,
        'file_path': describe_file(movie_file),
    })
    return target, None


def resolve_episode_replacement(target):
    service_name, base_url, api_key = get_arr_service(target)
    episode, error = get_episode(
        target['series_id'],
        target['season_number'],
        target['episode_number'],
        base_url=base_url,
        api_key=api_key,
    )
    if episode is None:
        return None, error

    episode_file_id = episode.get('episodeFileId')
    if not episode_file_id:
        file_path = 'Not currently downloaded'
    else:
        file_path = episode.get('episodeFile', {}).get('path') or f'File #{episode_file_id}'
    target.update({
        'service': service_name,
        'episode_id': episode.get('id'),
        'file_id': episode_file_id,
        'file_path': file_path,
    })
    return target, None


def process_radarr_redownload(target):
    movie_id = target['movie_id']
    _service_name, base_url, api_key = get_arr_service(target)
    queue_url = f"{normalize_base_url(base_url)}/api/v3/queue"
    history_url = f"{normalize_base_url(base_url)}/api/v3/history/movie"

    try:
        queue_items = extract_records(request_json(
            'GET',
            queue_url,
            headers=build_api_headers(api_key),
            params={'movieIds': movie_id},
        ))
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Radarr queue for movie %s: %s', movie_id, exc)
        return f'Radarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Radarr queue for movie %s: %s', movie_id, exc)
        return 'Unable to reach Radarr.'

    queue_item = find_queue_item(queue_items, 'movieId', movie_id)
    if queue_item is not None:
        success, error = delete_queue_item(base_url, api_key, queue_item['id'])
        if success:
            return f"Blacklisted and removed queued movie release for {target['label']}."
        return error

    try:
        history = request_json(
            'GET',
            history_url,
            headers=build_api_headers(api_key),
            params={'movieId': movie_id},
        ) or {}
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Radarr history for movie %s: %s', movie_id, exc)
        return f'Radarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Radarr history for movie %s: %s', movie_id, exc)
        return 'Unable to reach Radarr.'

    history_records = extract_records(history)
    imported_record = find_imported_history_record(history_records, 'movieId', movie_id)
    grabbed_record = None
    if imported_record is not None:
        grabbed_record = find_grabbed_record_for_import(history_records, imported_record, 'movieId', movie_id)
    if grabbed_record is None:
        grabbed_record = select_failed_history_record(history_records, 'movieId', movie_id)
    if grabbed_record is None:
        return 'No matching Radarr grabbed history entry was found for the current movie file.'

    success, error = mark_history_failed(base_url, api_key, grabbed_record['id'])
    if not success:
        return error

    if not target.get('file_id'):
        search_success, search_error = trigger_movie_search(base_url, api_key, movie_id)
        if search_success:
            return f"Blacklisted release for {target['label']} and triggered a fresh search. No current file was present to delete."
        return f"Blacklisted release for {target['label']}, but fresh search failed: {search_error}"

    delete_success, delete_error = delete_movie_file(base_url, api_key, target['file_id'])
    if not delete_success:
        return f"Blacklisted release for {target['label']}, but deleting the current file failed: {delete_error}"

    search_success, search_error = trigger_movie_search(base_url, api_key, movie_id)
    if not search_success:
        return f"Blacklisted release and deleted the current file for {target['label']}, but fresh search failed: {search_error}"

    return f"Blacklisted release, deleted the current file, and triggered a fresh search for {target['label']}."


def process_sonarr_redownload(target):
    _service_name, base_url, api_key = get_arr_service(target)
    episode_id = target['episode_id']
    queue_url = f"{normalize_base_url(base_url)}/api/v3/queue"
    history_url = f"{normalize_base_url(base_url)}/api/v3/history"

    try:
        queue_items = extract_records(request_json(
            'GET',
            queue_url,
            headers=build_api_headers(api_key),
            params={'seriesIds': target['series_id'], 'includeEpisode': 'true'},
        ))
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Sonarr queue for series %s: %s', target['series_id'], exc)
        return f'Sonarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Sonarr queue for series %s: %s', target['series_id'], exc)
        return 'Unable to reach Sonarr.'

    queue_item = find_queue_item(queue_items, 'episodeId', episode_id)
    if queue_item is not None:
        success, error = delete_queue_item(base_url, api_key, queue_item['id'])
        if success:
            return f"Blacklisted and removed queued episode release for {target['label']}."
        return error

    try:
        history = request_json(
            'GET',
            history_url,
            headers=build_api_headers(api_key),
            params={'episodeId': episode_id, 'includeEpisode': 'true', 'includeSeries': 'true'},
        ) or {}
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to fetch Sonarr history for episode %s: %s', episode_id, exc)
        return f'Sonarr request failed with status {status_code}.'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to fetch Sonarr history for episode %s: %s', episode_id, exc)
        return 'Unable to reach Sonarr.'

    history_records = extract_records(history)
    imported_record = find_imported_history_record(history_records, 'episodeId', episode_id)
    grabbed_record = None
    if imported_record is not None:
        grabbed_record = find_grabbed_record_for_import(history_records, imported_record, 'episodeId', episode_id)
    if grabbed_record is None:
        grabbed_record = select_failed_history_record(history_records, 'episodeId', episode_id)
    if grabbed_record is None:
        return 'No matching Sonarr grabbed history entry was found for the current episode file.'

    success, error = mark_history_failed(base_url, api_key, grabbed_record['id'])
    if not success:
        return error

    if not target.get('file_id'):
        search_success, search_error = trigger_episode_search(base_url, api_key, episode_id)
        if search_success:
            return f"Blacklisted release for {target['label']} and triggered a fresh search. No current file was present to delete."
        return f"Blacklisted release for {target['label']}, but fresh search failed: {search_error}"

    delete_success, delete_error = delete_episode_file(base_url, api_key, target['file_id'])
    if not delete_success:
        return f"Blacklisted release for {target['label']}, but deleting the current file failed: {delete_error}"

    search_success, search_error = trigger_episode_search(base_url, api_key, episode_id)
    if not search_success:
        return f"Blacklisted release and deleted the current file for {target['label']}, but fresh search failed: {search_error}"

    return f"Blacklisted release, deleted the current file, and triggered a fresh search for {target['label']}."


def build_redownload_confirmation(target):
    service = target.get('service') or ('Radarr4k' if target.get('is_4k') else 'Radarr')
    issue_line = f"Issue: #{target['issue_id']}\n" if target.get('issue_id') else ''
    file_line = f"Current file: {target.get('file_path', 'Unknown')}\n"
    return (
        f"Ready to replace the current release for {target['label']}.\n"
        f"{issue_line}"
        f"{file_line}"
        f'Service: {service}\n'
        'Actions: blocklist release, delete current file, search fresh release.'
    )


ISSUE_STATUS_OPEN = 1
ISSUE_STATUS_RESOLVED = 2


def is_issue_open(issue):
    return issue.get('status') == ISSUE_STATUS_OPEN


def resolve_seerr_issue(issue_id):
    url = f"{normalize_base_url(cfg.SEERR_BASE_URL)}/api/v1/issue/{issue_id}/resolved"
    try:
        request_json('POST', url, headers=build_api_headers(cfg.SEERR_API_KEY))
        return True, None
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 'unknown'
        logging.error('Unable to resolve Seerr issue %s: %s', issue_id, exc)
        return False, f'Failed to resolve Seerr issue (status {status_code}).'
    except requests.exceptions.RequestException as exc:
        logging.error('Unable to resolve Seerr issue %s: %s', issue_id, exc)
        return False, 'Unable to reach Seerr to resolve issue.'


def resolve_redownload_issue(url):
    reference, error = parse_seerr_reference(url)
    if reference is None:
        return None, error

    if reference['reference_type'] == 'media' and reference['media_type'] == 'tv':
        return None, 'TV replacements require an episode-linked Seerr issue URL.'

    media_details = {}
    if reference['reference_type'] == 'issue':
        issue_id = reference['issue_id']
        issue, error = get_seerr_issue(issue_id)
        if issue is None:
            return None, error
    else:
        issue, media_details, error = find_seerr_issue_for_media(reference['media_type'], reference['tmdb_id'])
        if issue is None:
            return None, error
        issue_id = issue['id']

    if not is_issue_open(issue):
        return None, f'Seerr issue #{issue_id} is already resolved.'

    target, error = get_issue_target(issue)
    if target is None:
        return None, error

    target['issue_id'] = issue_id
    if media_details:
        target['label'] = build_target_label(issue, media_details, target)
    if target['media_type'] == 'movie':
        return resolve_movie_replacement(target)
    return resolve_episode_replacement(target)


def execute_redownload(target):
    if target['media_type'] == 'movie':
        result = process_radarr_redownload(target)
    elif target['media_type'] == 'episode':
        result = process_sonarr_redownload(target)
    else:
        return 'Unsupported redownload target.'

    issue_id = target.get('issue_id')
    if issue_id and 'Blacklisted' in (result or ''):
        success, error = resolve_seerr_issue(issue_id)
        if success:
            result += f' Seerr issue #{issue_id} has been resolved.'
        else:
            result += f' Warning: {error}'

    return result
