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
    for tab in ('twins', 'panel', 'pilot', 'greenlight', 'launch', 'trailer', 'titles', 'norms'):
        assert f'data-tab="{tab}"' in html


if __name__ == '__main__':
    test_store_aliases_and_star()
    test_emit_missing_is_all()
    test_tab_allowed_admin_and_star()
    test_sony_three_tab_grant()
    test_pilot_mode_maps_to_tab()
    test_api_path_tabs()
    test_index_hides_denied_tabs()
    test_admin_tab_checkboxes()
    print('ok')
