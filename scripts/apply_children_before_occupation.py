#!/usr/bin/env python3
"""Reorder Demographics tab: Age of Children + Number of Children now
render ABOVE Occupation (was below).

Per Jenna 2026-09-02: "move number of children and age of children to
above occupation in demographics".

Single byte-safe splice: capture the full [Occupation, Age of Children,
Number of Children] block and rewrite as [Age of Children, Number of
Children, Occupation]. The promotion comment travels with the children
cards.

Idempotent: no-op if the new order is already in place.
"""
from pathlib import Path
import sys

INDEX = Path(__file__).resolve().parents[1] / 'templates' / 'index.html'

OLD_ORDER = """                            <div class="chart-card chart-card-occupation" data-module="Occupation">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Occupation">💼 OCCUPATION</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="occupationChartType" onchange="changeChartType('occupation', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="occupationSortSelect" onchange="sortOccupationChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">A-Z</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Occupation')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Occupation')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Occupation')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container occupation-chart-container" id="occupationChartContainer">
                                    <canvas id="occupationChart"></canvas>
                                </div>
                                <div class="demo-table-container" id="occupationTableContainer" style="display:none; max-height:600px;"></div>
                                <div id="occupationGpActualsTable" style="margin-top:0.75rem;"></div>
                            </div>
                            <!-- AGE_OF_CHILDREN + NUMBER_OF_CHILDREN promoted to first-class
                                 Demographics tiles (2026-08-28, Jenna). Both are pipeline-
                                 canonical (canonical_demos.PIPELINE_DEMO_SCHEMA); rendered
                                 same as Parental Status via renderCharts(). -->
                            <div class="chart-card" data-module="Age of Children">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Age of Children">🧒 AGE OF CHILDREN</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="ageOfChildrenChartType" onchange="changeChartType('ageOfChildren', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="ageOfChildrenSortSelect" onchange="sortAgeOfChildrenChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">Ordinal</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Age of Children')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Age of Children')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Age of Children')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container" id="ageOfChildrenChartContainer"><canvas id="ageOfChildrenChart"></canvas></div>
                                <div class="demo-table-container" id="ageOfChildrenTableContainer" style="display:none;"></div>
                            </div>
                            <div class="chart-card" data-module="Number of Children">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Number of Children">👨‍👩‍👧 NUMBER OF CHILDREN</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="numberOfChildrenChartType" onchange="changeChartType('numberOfChildren', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="numberOfChildrenSortSelect" onchange="sortNumberOfChildrenChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">Ordinal</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Number of Children')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Number of Children')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Number of Children')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container" id="numberOfChildrenChartContainer"><canvas id="numberOfChildrenChart"></canvas></div>
                                <div class="demo-table-container" id="numberOfChildrenTableContainer" style="display:none;"></div>
                            </div>"""

NEW_ORDER = """                            <!-- AGE_OF_CHILDREN + NUMBER_OF_CHILDREN promoted to first-class
                                 Demographics tiles (2026-08-28, Jenna). Both are pipeline-
                                 canonical (canonical_demos.PIPELINE_DEMO_SCHEMA); rendered
                                 same as Parental Status via renderCharts(). Reordered
                                 above Occupation per Jenna 2026-09-02. -->
                            <div class="chart-card" data-module="Age of Children">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Age of Children">🧒 AGE OF CHILDREN</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="ageOfChildrenChartType" onchange="changeChartType('ageOfChildren', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="ageOfChildrenSortSelect" onchange="sortAgeOfChildrenChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">Ordinal</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Age of Children')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Age of Children')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Age of Children')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container" id="ageOfChildrenChartContainer"><canvas id="ageOfChildrenChart"></canvas></div>
                                <div class="demo-table-container" id="ageOfChildrenTableContainer" style="display:none;"></div>
                            </div>
                            <div class="chart-card" data-module="Number of Children">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Number of Children">👨‍👩‍👧 NUMBER OF CHILDREN</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="numberOfChildrenChartType" onchange="changeChartType('numberOfChildren', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="numberOfChildrenSortSelect" onchange="sortNumberOfChildrenChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">Ordinal</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Number of Children')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Number of Children')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Number of Children')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container" id="numberOfChildrenChartContainer"><canvas id="numberOfChildrenChart"></canvas></div>
                                <div class="demo-table-container" id="numberOfChildrenTableContainer" style="display:none;"></div>
                            </div>
                            <div class="chart-card chart-card-occupation" data-module="Occupation">
                                <h3>
                                    <span class="chart-card-title-row">
                                        <span class="has-tooltip" data-tooltip="Occupation">💼 OCCUPATION</span>
                                    </span>
                                </h3>
                                <h3>
                                    <span class="chart-card-controls-row">
                                        <select id="occupationChartType" onchange="changeChartType('occupation', this.value)" class="chart-type-select">
                                            <option value="bar">📊</option>
                                            <option value="column">▮ Col</option>
                                            <option value="doughnut">🍩</option>
                                            <option value="pie">🥧</option>
                                            <option value="polarArea">🎯</option>
                                            <option value="table">📋 Table</option>
                                        </select>
                                        <select id="occupationSortSelect" onchange="sortOccupationChart(this.value)" class="chart-type-select has-tooltip" data-tooltip="Sort">
                                            <option value="value">%↓</option>
                                            <option value="alpha">A-Z</option>
                                            <option value="index">Idx↓</option>
                                        </select>
                                        <span class="module-action-btns"><button class="module-btn csv-btn has-tooltip" data-tooltip="Export CSV" onclick="exportModuleCSV('demographic', 'Occupation')">CSV</button><button class="module-btn png-btn has-tooltip" data-tooltip="Export PNG" onclick="exportModulePNG('demographic', 'Occupation')">PNG</button><button class="module-btn deck-btn has-tooltip" data-tooltip="Add to Deck" onclick="addModuleToDeck('demographic', 'Occupation')">➕</button></span>
                                    </span>
                                </h3>
                                <div class="chart-container occupation-chart-container" id="occupationChartContainer">
                                    <canvas id="occupationChart"></canvas>
                                </div>
                                <div class="demo-table-container" id="occupationTableContainer" style="display:none; max-height:600px;"></div>
                                <div id="occupationGpActualsTable" style="margin-top:0.75rem;"></div>
                            </div>"""


def main() -> int:
    src = INDEX.read_text(encoding='utf-8')
    if NEW_ORDER in src and OLD_ORDER not in src:
        print("[skip] already in new order")
        return 0
    count = src.count(OLD_ORDER)
    if count == 0:
        raise RuntimeError("OLD_ORDER anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"OLD_ORDER anchor found {count}x (must be unique)")
    bytes_before = len(src.encode('utf-8'))
    src = src.replace(OLD_ORDER, NEW_ORDER)
    INDEX.write_text(src, encoding='utf-8')
    bytes_after = len(src.encode('utf-8'))
    print(f"reordered [Occupation, Age, Number] -> [Age, Number, Occupation]")
    print(f"delta = {bytes_after - bytes_before:+d} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
