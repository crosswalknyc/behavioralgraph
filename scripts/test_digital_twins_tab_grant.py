#!/usr/bin/env python3
"""Digital Twins per-tab grants: store, emit, API path, UI hide."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from migration import digital_twins as DT


def test_store_aliases_and_star():
    assert DT.store_tab_grant(None) == ['*']
    assert DT.store_tab_grant(['*']) == ['*']
    assert DT.store_tab_grant(['twins', 'focus group', 'pilot testing']) == [
        'twins', 'panel', 'pilot',
    ]
    assert DT.store_tab_grant(['creation', 'Focus Group IQ', 'Pilot']) == [
        'twins', 'panel', 'pilot',
    ]
    assert DT.store_tab_grant([]) == []
    assert DT.store_tab_grant(['greenlight', 'script', 'concept']) == ['greenlight']


def test_emit_missing_is_all():
    assert DT.emit_tab_grant(None) == ['*']
    assert DT.emit_tab_grant(['twins', 'panel']) == ['twins', 'panel']
    assert DT.emit_tab_grant([]) == []


def test_tab_allowed_admin_and_star():
    admin = {'role': 'admin', 'allowed_digital_twins_tabs': ['panel']}
    assert DT.tab_allowed(admin, 'greenlight') is True
    star = {'role': 'user', 'allowed_digital_twins_tabs': ['*']}
    assert DT.tab_allowed(star, 'launch') is True
    missing = {'role': 'user'}
    assert DT.tab_allowed(missing, 'norms') is True


def test_sony_three_tab_grant():
    user = {
        'role': 'user',
        'has_digital_twins_iq_access': True,
        'allowed_digital_twins_tabs': ['twins', 'panel', 'pilot'],
    }
    assert DT.tab_allowed(user, 'twins') is True
    assert DT.tab_allowed(user, 'creation') is True
    assert DT.tab_allowed(user, 'panel') is True
    assert DT.tab_allowed(user, 'focus_group') is True
    assert DT.tab_allowed(user, 'pilot') is True
    assert DT.tab_allowed(user, 'greenlight') is False
    assert DT.tab_allowed(user, 'launch') is False
    assert DT.tab_allowed(user, 'spend') is False
    assert DT.tab_allowed(user, 'trailer') is False
    assert DT.tab_allowed(user, 'titles') is False
    assert DT.tab_allowed(user, 'norms') is False


def test_pilot_mode_maps_to_tab():
    assert DT.tab_for_pilot_mode('pilot') == 'pilot'
    assert DT.tab_for_pilot_mode('feature') == 'pilot'
    assert DT.tab_for_pilot_mode('script') == 'greenlight'
    assert DT.tab_for_pilot_mode('concept') == 'greenlight'
    assert DT.tab_for_pilot_mode('trailer') == 'trailer'
    assert DT.tab_for_pilot_mode('title') == 'titles'
    user = {'role': 'user', 'allowed_digital_twins_tabs': ['twins', 'panel', 'pilot']}
    assert DT.tab_allowed(user, DT.tab_for_pilot_mode('pilot')) is True
    assert DT.tab_allowed(user, DT.tab_for_pilot_mode('script')) is False


def test_api_path_tabs():
    assert DT.tab_for_api_path('/api/digital-twins/list') == 'twins'
    assert DT.tab_for_api_path('/api/digital-twins/build') == 'twins'
    assert DT.tab_for_api_path('/api/digital-twins/how-to') is None
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/panel/open') == 'panel'
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/run') == 'launch'
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/spend/activate') == 'launch'
    assert DT.tab_for_api_path('/api/digital-twins/pilot-norms') == 'norms'
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/pilot/open') is None
    # Predictive Analytics (2026-11-18) is its own tab; its routes and aliases resolve to it
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/guided/plan') == 'predict'
    assert DT.tab_for_api_path('/api/digital-twins/digital-twins/x.json.gz/guided/abc/report.pdf') == 'predict'
    assert DT.normalize_tab('predictive_analytics') == 'predict'
    assert DT.normalize_tab('Predictive') == 'predict'
    assert DT.tab_allowed({'role': 'user', DT.TABS_FIELD: ['predict']}, 'predict') is True
    assert DT.tab_allowed({'role': 'user', DT.TABS_FIELD: ['panel']}, 'predict') is False


def test_hidden_for_now_tabs():
    # Jenna 2026-11-18: Launch (hand-written constants) and Norms (empty until tests are filed) are hidden for now;
    # the grants and the routes stay, the how-to and the page leave them out
    assert DT.HIDDEN_TABS == ('greenlight', 'launch', 'trailer', 'titles', 'norms')
    assert DT.tab_hidden('launch') and DT.tab_hidden('spend') and DT.tab_hidden('norms') and DT.tab_hidden('greenlight') and DT.tab_hidden('script') and DT.tab_hidden('trailer') and DT.tab_hidden('titles')
    assert not DT.tab_hidden('panel') and not DT.tab_hidden('predict') and not DT.tab_hidden('pilot') and not DT.tab_hidden('twins')
    ids = [it['id'] for it in DT.how_to()['items']]
    for hidden in ('launch', 'spend', 'norms', 'greenlight', 'trailer', 'titles'):
        assert hidden not in ids, hidden
    assert ids and set(ids) <= {'twin', 'predict', 'panel', 'pilot'}
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'index.html'), encoding='utf-8').read()
    assert "var DTW_HIDDEN_TABS = ['greenlight', 'launch', 'trailer', 'titles', 'norms'];" in html
    for tab in DT.HIDDEN_TABS:
        assert f'<button type="button" hidden class="dtw-seg" data-tab="{tab}"' in html, tab
    for tab in ('twins', 'predict', 'panel', 'pilot'):
        assert f'<button type="button" class="dtw-seg{" active" if tab == "twins" else ""}" data-tab="{tab}"' in html, tab
    assert "if (DTW_HIDDEN_TABS.indexOf(tab) >= 0) tab = 'twins';" in html


def test_index_hides_denied_tabs():
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'index.html'), encoding='utf-8').read()
    assert 'window.__allowedDigitalTwinsTabs' in html
    assert 'window.dtwTabAllowed' in html
    assert 'window.dtwApplyTabGrant' in html
    assert '.dtw-seg[hidden]' in html
    assert "if (typeof window.dtwTabAllowed === 'function' && !window.dtwTabAllowed(tab))" in html


def test_admin_tab_checkboxes():
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'admin.html'), encoding='utf-8').read()
    assert 'allowAllDigitalTwinsTabs' in html
    assert 'allowed_digital_twins_tabs: getAllowedDigitalTwinsTabsPayload()' in html
    for tab in ('twins', 'predict', 'panel', 'pilot', 'greenlight', 'launch', 'trailer', 'titles', 'norms'):
        assert f'data-tab="{tab}"' in html


if __name__ == '__main__':
    test_store_aliases_and_star()
    test_emit_missing_is_all()
    test_tab_allowed_admin_and_star()
    test_sony_three_tab_grant()
    test_pilot_mode_maps_to_tab()
    test_api_path_tabs()
    test_hidden_for_now_tabs()
    test_index_hides_denied_tabs()
    test_admin_tab_checkboxes()
    print('ok')
