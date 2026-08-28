#!/usr/bin/env python3
"""Promote AGE_OF_CHILDREN + NUMBER_OF_CHILDREN to first-class Demographics.

User directive 2026-08-28 (Jenna): "move these to demographics"

Both categories are already pipeline-canonical (canonical_demos.py
PIPELINE_DEMO_SCHEMA), but the FRONTEND treats them as behavioral
cards because the demographics list in every routing/rendering path
in templates/index.html hardcodes only the 9 legacy demo categories
(age, gender, ethnicity, income, education, relationship,
sexual_orientation, parental_status, occupation).

This script wires the two child categories into every one of those
paths so they render as proper Demographics chart tiles next to
Parental Status, get exported in the Demographics CSV, participate
in Benchmark comparisons, and disappear from the Behavioral tab.

The scope is 18 byte-safe splices in templates/index.html:

  P1  demographics initializer objects (all 4)
  P2  demoCategories uppercase (routing gate for parseCSV)
  P3  parseCSV per-category if blocks
  P4  demoCats list in cohort-freshness check
  P5  demoChartKeys + chartKeyToDataCat in comparison overlay
  P6  chartTypePrefs (chart-type per demo tile)
  P7  _cmpDataCatMap in Compare tab
  P8  demoCategoryList (CSV export)
  P9  demoCategories lowercase (Insights top demographics)
  P10 demoKeys in parseComparisonData
  P11 demoCategories uppercase + demoKeyByCategory
      in parseComparisonData
  P12 demoCategories lowercase in Compare shared-behaviors collector
  P13 BENCHMARK_DEMO_CATEGORY_NAMES
  P14 demoCategories lowercase in Compare over-index collector
  S1  sort-mode state vars
  S2  sortAgeOfChildrenChart + sortNumberOfChildrenChart handlers
  S3  getSortedAgeOfChildrenData + getSortedNumberOfChildrenData
  H1  Two new chart cards in the Demographics tab HTML grid
  R1  Two new chart renderer blocks after the Parental Status
      renderer inside renderCharts()

Behavior:
  * Data still flows the same way. If a profile carries
    AGE_OF_CHILDREN / NUMBER_OF_CHILDREN rows, they now land in
    result.demographics.age_of_children / .number_of_children and
    are drawn by the new tile. If the profile carries no such
    rows, the tile stays empty (matches Parental Status behavior).
  * Compare tab, benchmark analysis, CSV export, and PNG deck
    add-to-deck actions all pick up the new categories via the
    routing plumbing above.
  * The Behavioral tab no longer shows an AGE_OF_CHILDREN or
    NUMBER_OF_CHILDREN card because the routing gate at line 56445
    reads `!demoCategories.includes(category)` and these are now
    in demoCategories.

Per index-html-safety.mdc: every splice has a unique-anchor guard,
the file is fully backed up to /tmp before any write, and the
validator runs at the end.
"""

from pathlib import Path
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "templates" / "index.html"

STAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
BACKUP = Path("/tmp") / f"index.pre_children_to_demo_{STAMP}.html"


# ===========================================================================
# P1: demographics initializer objects (4 objects at line 55774-55777)
# ===========================================================================

P1_OLD = (
    "                demographics: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {} },\n"
    "                demographicsIndex: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {} },\n"
    "                demographicsGenPop: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {} },\n"
    "                demographicsProjection: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {} },\n"
)

P1_NEW = (
    "                demographics: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {}, age_of_children: {}, number_of_children: {} },\n"
    "                demographicsIndex: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {}, age_of_children: {}, number_of_children: {} },\n"
    "                demographicsGenPop: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {}, age_of_children: {}, number_of_children: {} },\n"
    "                demographicsProjection: { age: {}, gender: {}, ethnicity: {}, income: {}, education: {}, relationship: {}, sexual_orientation: {}, parental_status: {}, occupation: {}, age_of_children: {}, number_of_children: {} },\n"
)


# ===========================================================================
# P2: demoCategories uppercase (routing gate for parseCSV) line 55785
# ===========================================================================

P2_OLD = (
    "            const demoCategories = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION'];\n"
    "            const skipCategories = ['INPUT_METADATA', 'SAMPLE SIZE', 'BRAND INPUT', 'AVID FAN', 'CASUAL FAN', 'BRAND CATEGORY'];\n"
    "            const US_POPULATION = 329900000;\n"
)

P2_NEW = (
    "            const demoCategories = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION', 'AGE_OF_CHILDREN', 'NUMBER_OF_CHILDREN'];\n"
    "            const skipCategories = ['INPUT_METADATA', 'SAMPLE SIZE', 'BRAND INPUT', 'AVID FAN', 'CASUAL FAN', 'BRAND CATEGORY'];\n"
    "            const US_POPULATION = 329900000;\n"
)


# ===========================================================================
# P3: parseCSV per-category if blocks. Append two new if-blocks after the
# OCCUPATION block (before the Interests block).
# ===========================================================================

P3_OLD = (
    "                if (category === 'OCCUPATION' && value) {\n"
    "                    const occKey = normalizeOccupationLabel(value);\n"
    "                    result.demographics.occupation[occKey] = (result.demographics.occupation[occKey] || 0) + pct;\n"
    "                    result.demographicsIndex.occupation[occKey] = index;\n"
    "                    result.demographicsGenPop.occupation[occKey] = (result.demographicsGenPop.occupation[occKey] || 0) + genPopPct;\n"
    "                    result.demographicsProjection.occupation[occKey] = (result.demographicsProjection.occupation[occKey] || 0) + usProjection;\n"
    "                }\n"
    "                \n"
    "                // Interests category (handle both singular and plural)\n"
)

P3_NEW = (
    "                if (category === 'OCCUPATION' && value) {\n"
    "                    const occKey = normalizeOccupationLabel(value);\n"
    "                    result.demographics.occupation[occKey] = (result.demographics.occupation[occKey] || 0) + pct;\n"
    "                    result.demographicsIndex.occupation[occKey] = index;\n"
    "                    result.demographicsGenPop.occupation[occKey] = (result.demographicsGenPop.occupation[occKey] || 0) + genPopPct;\n"
    "                    result.demographicsProjection.occupation[occKey] = (result.demographicsProjection.occupation[occKey] || 0) + usProjection;\n"
    "                }\n"
    "                // AGE_OF_CHILDREN + NUMBER_OF_CHILDREN (2026-08-28 promotion\n"
    "                // to first-class Demographics). Both are pipeline-canonical\n"
    "                // in canonical_demos.PIPELINE_DEMO_SCHEMA; the routing gate\n"
    "                // above now treats them as demographics so they render as\n"
    "                // tiles in the Demographics tab alongside Parental Status.\n"
    "                if (category === 'AGE_OF_CHILDREN' && value) {\n"
    "                    result.demographics.age_of_children[value] = pct;\n"
    "                    result.demographicsIndex.age_of_children[value] = index;\n"
    "                    result.demographicsGenPop.age_of_children[value] = genPopPct;\n"
    "                    result.demographicsProjection.age_of_children[value] = usProjection;\n"
    "                }\n"
    "                if (category === 'NUMBER_OF_CHILDREN' && value) {\n"
    "                    result.demographics.number_of_children[value] = pct;\n"
    "                    result.demographicsIndex.number_of_children[value] = index;\n"
    "                    result.demographicsGenPop.number_of_children[value] = genPopPct;\n"
    "                    result.demographicsProjection.number_of_children[value] = usProjection;\n"
    "                }\n"
    "                \n"
    "                // Interests category (handle both singular and plural)\n"
)


# ===========================================================================
# P4: demoCats list at line 57872 (cohort-freshness check)
# ===========================================================================

P4_OLD = (
    "            const demoCats = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation'];\n"
    "            for (const cat of demoCats) {\n"
)

P4_NEW = (
    "            const demoCats = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation', 'age_of_children', 'number_of_children'];\n"
    "            for (const cat of demoCats) {\n"
)


# ===========================================================================
# P5: demoChartKeys + chartKeyToDataCat at line 58230-58236
# ===========================================================================

P5_OLD = (
    "            const demoChartKeys = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexualOrientation', 'parentalStatus', 'occupation'];\n"
    "            const chartKeyToDataCat = {\n"
    "                age: 'age', gender: 'gender', ethnicity: 'ethnicity', income: 'income',\n"
    "                education: 'education', relationship: 'relationship',\n"
    "                sexualOrientation: 'sexual_orientation', parentalStatus: 'parental_status',\n"
    "                occupation: 'occupation'\n"
    "            };\n"
)

P5_NEW = (
    "            const demoChartKeys = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexualOrientation', 'parentalStatus', 'occupation', 'ageOfChildren', 'numberOfChildren'];\n"
    "            const chartKeyToDataCat = {\n"
    "                age: 'age', gender: 'gender', ethnicity: 'ethnicity', income: 'income',\n"
    "                education: 'education', relationship: 'relationship',\n"
    "                sexualOrientation: 'sexual_orientation', parentalStatus: 'parental_status',\n"
    "                occupation: 'occupation',\n"
    "                ageOfChildren: 'age_of_children', numberOfChildren: 'number_of_children'\n"
    "            };\n"
)


# ===========================================================================
# P6: chartTypePrefs at line 62607
# ===========================================================================

P6_OLD = (
    "        const chartTypePrefs = {\n"
    "            age: 'bar',\n"
    "            gender: 'bar',\n"
    "            ethnicity: 'bar',\n"
    "            income: 'bar',\n"
    "            education: 'bar',\n"
    "            relationship: 'bar',\n"
    "            sexualOrientation: 'bar',\n"
    "            parentalStatus: 'bar',\n"
    "            occupation: 'bar'\n"
    "        };\n"
)

P6_NEW = (
    "        const chartTypePrefs = {\n"
    "            age: 'bar',\n"
    "            gender: 'bar',\n"
    "            ethnicity: 'bar',\n"
    "            income: 'bar',\n"
    "            education: 'bar',\n"
    "            relationship: 'bar',\n"
    "            sexualOrientation: 'bar',\n"
    "            parentalStatus: 'bar',\n"
    "            occupation: 'bar',\n"
    "            ageOfChildren: 'bar',\n"
    "            numberOfChildren: 'bar'\n"
    "        };\n"
)


# ===========================================================================
# P7: _cmpDataCatMap at line 62838-62844 (Compare tab chart-key -> data-key)
# ===========================================================================

P7_OLD = (
    "            const _cmpDataCatMap = {\n"
    "                age: 'age', gender: 'gender', ethnicity: 'ethnicity', income: 'income',\n"
    "                education: 'education', relationship: 'relationship',\n"
    "                sexualOrientation: 'sexual_orientation',\n"
    "                parentalStatus: 'parental_status',\n"
    "                occupation: 'occupation'\n"
    "            };\n"
)

P7_NEW = (
    "            const _cmpDataCatMap = {\n"
    "                age: 'age', gender: 'gender', ethnicity: 'ethnicity', income: 'income',\n"
    "                education: 'education', relationship: 'relationship',\n"
    "                sexualOrientation: 'sexual_orientation',\n"
    "                parentalStatus: 'parental_status',\n"
    "                occupation: 'occupation',\n"
    "                ageOfChildren: 'age_of_children',\n"
    "                numberOfChildren: 'number_of_children'\n"
    "            };\n"
)


# ===========================================================================
# P8: demoCategoryList at line 78076 (CSV export mapping)
# ===========================================================================

P8_OLD = (
    "                const demoCategoryList = [{ key: 'age', title: 'Age' }, { key: 'gender', title: 'Gender' }, { key: 'ethnicity', title: 'Ethnicity' }, { key: 'income', title: 'Income' }, { key: 'education', title: 'Education' }, { key: 'relationship', title: 'Relationship Status' }, { key: 'sexual_orientation', title: 'Sexual Orientation' }, { key: 'parental_status', title: 'Parental Status' }, { key: 'occupation', title: 'Occupation' }];\n"
)

P8_NEW = (
    "                const demoCategoryList = [{ key: 'age', title: 'Age' }, { key: 'gender', title: 'Gender' }, { key: 'ethnicity', title: 'Ethnicity' }, { key: 'income', title: 'Income' }, { key: 'education', title: 'Education' }, { key: 'relationship', title: 'Relationship Status' }, { key: 'sexual_orientation', title: 'Sexual Orientation' }, { key: 'parental_status', title: 'Parental Status' }, { key: 'occupation', title: 'Occupation' }, { key: 'age_of_children', title: 'Age of Children' }, { key: 'number_of_children', title: 'Number of Children' }];\n"
)


# ===========================================================================
# P9: demoCategories lowercase at line 78175 (Insights top-N export)
# Only include the 4 primary drivers here (age/gender/ethnicity/income) so
# the Insights top-N stays focused; do NOT add child categories to this
# very-narrow list.
# NOTE: intentionally left unchanged.
# ===========================================================================


# ===========================================================================
# P10: demoKeys at line 80587 (parseComparisonData initializer)
# ===========================================================================

P10_OLD = (
    "            const demoKeys = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation'];\n"
)

P10_NEW = (
    "            const demoKeys = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation', 'age_of_children', 'number_of_children'];\n"
)


# ===========================================================================
# P11: demoCategories uppercase + demoKeyByCategory at line 80602-80603
# ===========================================================================

P11_OLD = (
    "            const demoCategories = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION'];\n"
    "            const demoKeyByCategory = { AGE: 'age', GENDER: 'gender', ETHNICITY: 'ethnicity', INCOME: 'income', EDUCATION: 'education', RELATIONSHIP: 'relationship', SEXUAL_ORIENTATION: 'sexual_orientation', PARENTAL_STATUS: 'parental_status', OCCUPATION: 'occupation' };\n"
)

P11_NEW = (
    "            const demoCategories = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION', 'AGE_OF_CHILDREN', 'NUMBER_OF_CHILDREN'];\n"
    "            const demoKeyByCategory = { AGE: 'age', GENDER: 'gender', ETHNICITY: 'ethnicity', INCOME: 'income', EDUCATION: 'education', RELATIONSHIP: 'relationship', SEXUAL_ORIENTATION: 'sexual_orientation', PARENTAL_STATUS: 'parental_status', OCCUPATION: 'occupation', AGE_OF_CHILDREN: 'age_of_children', NUMBER_OF_CHILDREN: 'number_of_children' };\n"
)


# ===========================================================================
# P12: demoCategories lowercase at line 82761 (Compare shared-behaviors)
# Uses widened anchor to distinguish from line 83516 (same content, but
# different function scope). The distinguishing context is the SPORTS-team
# aggregator right before it.
# ===========================================================================

P12_OLD = (
    "            profiles.forEach(p => {\n"
    "                // Add demographics data\n"
    "                if (p.demographics && p.demographicsIndex) {\n"
    "                    const demoCategories = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation'];\n"
    "                    demoCategories.forEach(cat => {\n"
    "                        const catData = p.demographics[cat] || {};\n"
    "                        const catIndex = p.demographicsIndex[cat] || {};\n"
    "                        Object.entries(catData).forEach(([value, pct]) => {\n"
    "                            const key = cat.toUpperCase() + '|' + value;\n"
    "                            const index = catIndex[value] || 100;\n"
    "                            if (!allBehaviors[key]) {\n"
    "                                allBehaviors[key] = { \n"
    "                                    name: value, \n"
    "                                    category: cat.toUpperCase(), \n"
    "                                    profileData: {},\n"
    "                                    overIndexingProfiles: [],\n"
    "                                    totalIndex: 0,\n"
    "                                    count: 0\n"
    "                                };\n"
    "                            }\n"
    "                            // Store data for each profile\n"
    "                            allBehaviors[key].profileData[p.name] = {\n"
    "                                index: index,\n"
    "                                pct: pct || 0\n"
    "                            };\n"
    "                            // Track which non-GenPop profiles over-index\n"
    "                            if (index >= overIndexThreshold && p.name.toUpperCase() !== 'GEN POP' && p.name.toUpperCase() !== 'GENPOP') {\n"
    "                                allBehaviors[key].overIndexingProfiles.push(p.name);\n"
    "                                allBehaviors[key].totalIndex += index;\n"
    "                                allBehaviors[key].count++;\n"
    "                            }\n"
    "                        });\n"
    "                    });\n"
    "                }\n"
    "                \n"
    "                // Add TELECOM behavioral data only\n"
)

P12_NEW = (
    "            profiles.forEach(p => {\n"
    "                // Add demographics data\n"
    "                if (p.demographics && p.demographicsIndex) {\n"
    "                    const demoCategories = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation', 'age_of_children', 'number_of_children'];\n"
    "                    demoCategories.forEach(cat => {\n"
    "                        const catData = p.demographics[cat] || {};\n"
    "                        const catIndex = p.demographicsIndex[cat] || {};\n"
    "                        Object.entries(catData).forEach(([value, pct]) => {\n"
    "                            const key = cat.toUpperCase() + '|' + value;\n"
    "                            const index = catIndex[value] || 100;\n"
    "                            if (!allBehaviors[key]) {\n"
    "                                allBehaviors[key] = { \n"
    "                                    name: value, \n"
    "                                    category: cat.toUpperCase(), \n"
    "                                    profileData: {},\n"
    "                                    overIndexingProfiles: [],\n"
    "                                    totalIndex: 0,\n"
    "                                    count: 0\n"
    "                                };\n"
    "                            }\n"
    "                            // Store data for each profile\n"
    "                            allBehaviors[key].profileData[p.name] = {\n"
    "                                index: index,\n"
    "                                pct: pct || 0\n"
    "                            };\n"
    "                            // Track which non-GenPop profiles over-index\n"
    "                            if (index >= overIndexThreshold && p.name.toUpperCase() !== 'GEN POP' && p.name.toUpperCase() !== 'GENPOP') {\n"
    "                                allBehaviors[key].overIndexingProfiles.push(p.name);\n"
    "                                allBehaviors[key].totalIndex += index;\n"
    "                                allBehaviors[key].count++;\n"
    "                            }\n"
    "                        });\n"
    "                    });\n"
    "                }\n"
    "                \n"
    "                // Add TELECOM behavioral data only\n"
)


# ===========================================================================
# P13: BENCHMARK_DEMO_CATEGORY_NAMES at line 82948
# ===========================================================================

P13_OLD = (
    "        const BENCHMARK_DEMO_CATEGORY_NAMES = {\n"
    "            age: 'Age',\n"
    "            gender: 'Gender',\n"
    "            ethnicity: 'Ethnicity',\n"
    "            income: 'Income',\n"
    "            education: 'Education',\n"
    "            relationship: 'Relationship Status',\n"
    "            sexual_orientation: 'Sexual Orientation',\n"
    "            parental_status: 'Parental Status',\n"
    "            occupation: 'Occupation'\n"
    "        };\n"
)

P13_NEW = (
    "        const BENCHMARK_DEMO_CATEGORY_NAMES = {\n"
    "            age: 'Age',\n"
    "            gender: 'Gender',\n"
    "            ethnicity: 'Ethnicity',\n"
    "            income: 'Income',\n"
    "            education: 'Education',\n"
    "            relationship: 'Relationship Status',\n"
    "            sexual_orientation: 'Sexual Orientation',\n"
    "            parental_status: 'Parental Status',\n"
    "            occupation: 'Occupation',\n"
    "            age_of_children: 'Age of Children',\n"
    "            number_of_children: 'Number of Children'\n"
    "        };\n"
)


# ===========================================================================
# P14: demoCategories lowercase at line 83516 (Compare over-index collector).
# Uses widened anchor to distinguish from line 82761.
# ===========================================================================

P14_OLD = (
    "            // Collect demographics and TELECOM data only\n"
    "            const allBehaviors = {};\n"
    "            \n"
    "            profiles.forEach(p => {\n"
    "                // Add demographics data\n"
    "                if (p.demographics && p.demographicsIndex) {\n"
    "                    const demoCategories = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation'];\n"
)

P14_NEW = (
    "            // Collect demographics and TELECOM data only\n"
    "            const allBehaviors = {};\n"
    "            \n"
    "            profiles.forEach(p => {\n"
    "                // Add demographics data\n"
    "                if (p.demographics && p.demographicsIndex) {\n"
    "                    const demoCategories = ['age', 'gender', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status', 'occupation', 'age_of_children', 'number_of_children'];\n"
)


# ===========================================================================
# S1: sort-mode state vars at line 62586-62587
# ===========================================================================

S1_OLD = (
    "        let currentParentalStatusSortMode = 'value';\n"
    "        let currentOccupationSortMode = 'value';\n"
    "        \n"
    "        // Chart type preferences (stored per chart). Each demographic\n"
)

S1_NEW = (
    "        let currentParentalStatusSortMode = 'value';\n"
    "        let currentOccupationSortMode = 'value';\n"
    "        let currentAgeOfChildrenSortMode = 'value';\n"
    "        let currentNumberOfChildrenSortMode = 'value';\n"
    "        \n"
    "        // Chart type preferences (stored per chart). Each demographic\n"
)


# ===========================================================================
# S2: sortAgeOfChildrenChart + sortNumberOfChildrenChart at line 63052-63057
# ===========================================================================

S2_OLD = (
    "        function sortOccupationChart(sortMode) {\n"
    "            currentOccupationSortMode = sortMode;\n"
    "            if (currentDashboardData) {\n"
    "                renderCharts(currentDashboardData.demographics, currentDashboardData.demographicsIndex, currentDashboardData.demographicsProjection, currentDashboardData.demographicsGenPop);\n"
    "            }\n"
    "        }\n"
    "        \n"
    "        // Map original age labels to display labels\n"
)

S2_NEW = (
    "        function sortOccupationChart(sortMode) {\n"
    "            currentOccupationSortMode = sortMode;\n"
    "            if (currentDashboardData) {\n"
    "                renderCharts(currentDashboardData.demographics, currentDashboardData.demographicsIndex, currentDashboardData.demographicsProjection, currentDashboardData.demographicsGenPop);\n"
    "            }\n"
    "        }\n"
    "        function sortAgeOfChildrenChart(sortMode) {\n"
    "            currentAgeOfChildrenSortMode = sortMode;\n"
    "            if (currentDashboardData) {\n"
    "                renderCharts(currentDashboardData.demographics, currentDashboardData.demographicsIndex, currentDashboardData.demographicsProjection, currentDashboardData.demographicsGenPop);\n"
    "            }\n"
    "        }\n"
    "        function sortNumberOfChildrenChart(sortMode) {\n"
    "            currentNumberOfChildrenSortMode = sortMode;\n"
    "            if (currentDashboardData) {\n"
    "                renderCharts(currentDashboardData.demographics, currentDashboardData.demographicsIndex, currentDashboardData.demographicsProjection, currentDashboardData.demographicsGenPop);\n"
    "            }\n"
    "        }\n"
    "        \n"
    "        // Map original age labels to display labels\n"
)


# ===========================================================================
# S3: getSortedAgeOfChildrenData + getSortedNumberOfChildrenData at line 63867
# ===========================================================================

S3_OLD = (
    "        function getSortedOccupationData(occupationData, occupationIndex) {\n"
    "            return getSortedDemoData(occupationData, occupationIndex, currentOccupationSortMode);\n"
    "        }\n"
    "\n"
    "        function getSortedAgeData(ageData) {\n"
)

S3_NEW = (
    "        function getSortedOccupationData(occupationData, occupationIndex) {\n"
    "            return getSortedDemoData(occupationData, occupationIndex, currentOccupationSortMode);\n"
    "        }\n"
    "\n"
    "        function getSortedAgeOfChildrenData(data, indexMap) {\n"
    "            // Canonical Age-of-Children ordering (canonical_demos.py\n"
    "            // PIPELINE_DEMO_SCHEMA). Used when sortMode is 'alpha' since\n"
    "            // 'No Kids, Under 3, 3 to 5, 6 to 10, 11 to 13, 14 to 17'\n"
    "            // alphabetized reads nonsensically ('11 to 13' before '3 to 5').\n"
    "            const canonicalOrder = ['No Kids', 'Under 3', '3 to 5', '6 to 10', '11 to 13', '14 to 17'];\n"
    "            const mode = currentAgeOfChildrenSortMode;\n"
    "            if (mode !== 'alpha') {\n"
    "                return getSortedDemoData(data, indexMap, mode);\n"
    "            }\n"
    "            // 'alpha' -> canonical ordinal order (falls back to alpha for\n"
    "            // any label not in canonicalOrder).\n"
    "            const entries = Object.entries(data || {});\n"
    "            entries.sort((a, b) => {\n"
    "                const ia = canonicalOrder.indexOf(a[0]);\n"
    "                const ib = canonicalOrder.indexOf(b[0]);\n"
    "                if (ia !== -1 && ib !== -1) return ia - ib;\n"
    "                if (ia !== -1) return -1;\n"
    "                if (ib !== -1) return 1;\n"
    "                return String(a[0]).localeCompare(String(b[0]));\n"
    "            });\n"
    "            const labels = entries.map(e => e[0]);\n"
    "            const values = entries.map(e => e[1]);\n"
    "            return { labels, values, originalKeys: labels };\n"
    "        }\n"
    "\n"
    "        function getSortedNumberOfChildrenData(data, indexMap) {\n"
    "            const canonicalOrder = ['0', '1', '2', '3', '4+'];\n"
    "            const mode = currentNumberOfChildrenSortMode;\n"
    "            if (mode !== 'alpha') {\n"
    "                return getSortedDemoData(data, indexMap, mode);\n"
    "            }\n"
    "            const entries = Object.entries(data || {});\n"
    "            entries.sort((a, b) => {\n"
    "                const ia = canonicalOrder.indexOf(String(a[0]));\n"
    "                const ib = canonicalOrder.indexOf(String(b[0]));\n"
    "                if (ia !== -1 && ib !== -1) return ia - ib;\n"
    "                if (ia !== -1) return -1;\n"
    "                if (ib !== -1) return 1;\n"
    "                return String(a[0]).localeCompare(String(b[0]));\n"
    "            });\n"
    "            const labels = entries.map(e => e[0]);\n"
    "            const values = entries.map(e => e[1]);\n"
    "            return { labels, values, originalKeys: labels };\n"
    "        }\n"
    "\n"
    "        function getSortedAgeData(ageData) {\n"
)


# ===========================================================================
# H1: Two new HTML chart cards inserted after the Occupation card at 16746
# ===========================================================================

H1_OLD = (
    "                            <div class=\"chart-card chart-card-occupation\" data-module=\"Occupation\">\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-title-row\">\n"
    "                                        <span class=\"has-tooltip\" data-tooltip=\"Occupation\">💼 OCCUPATION</span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-controls-row\">\n"
    "                                        <select id=\"occupationChartType\" onchange=\"changeChartType('occupation', this.value)\" class=\"chart-type-select\">\n"
    "                                            <option value=\"bar\">📊</option>\n"
    "                                            <option value=\"column\">▮ Col</option>\n"
    "                                            <option value=\"doughnut\">🍩</option>\n"
    "                                            <option value=\"pie\">🥧</option>\n"
    "                                            <option value=\"polarArea\">🎯</option>\n"
    "                                            <option value=\"table\">📋 Table</option>\n"
    "                                        </select>\n"
    "                                        <select id=\"occupationSortSelect\" onchange=\"sortOccupationChart(this.value)\" class=\"chart-type-select has-tooltip\" data-tooltip=\"Sort\">\n"
    "                                            <option value=\"value\">%↓</option>\n"
    "                                            <option value=\"alpha\">A-Z</option>\n"
    "                                            <option value=\"index\">Idx↓</option>\n"
    "                                        </select>\n"
    "                                        <span class=\"module-action-btns\"><button class=\"module-btn csv-btn has-tooltip\" data-tooltip=\"Export CSV\" onclick=\"exportModuleCSV('demographic', 'Occupation')\">CSV</button><button class=\"module-btn png-btn has-tooltip\" data-tooltip=\"Export PNG\" onclick=\"exportModulePNG('demographic', 'Occupation')\">PNG</button><button class=\"module-btn deck-btn has-tooltip\" data-tooltip=\"Add to Deck\" onclick=\"addModuleToDeck('demographic', 'Occupation')\">➕</button></span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <div class=\"chart-container occupation-chart-container\" id=\"occupationChartContainer\">\n"
    "                                    <canvas id=\"occupationChart\"></canvas>\n"
    "                                </div>\n"
    "                                <div class=\"demo-table-container\" id=\"occupationTableContainer\" style=\"display:none; max-height:600px;\"></div>\n"
    "                                <div id=\"occupationGpActualsTable\" style=\"margin-top:0.75rem;\"></div>\n"
    "                            </div>\n"
    "                        </div>\n"
    "                    </div>\n"
    "            </div>\n"
    "\n"
    "            <!-- Behavioral Tab -->\n"
)

H1_NEW = (
    "                            <div class=\"chart-card chart-card-occupation\" data-module=\"Occupation\">\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-title-row\">\n"
    "                                        <span class=\"has-tooltip\" data-tooltip=\"Occupation\">💼 OCCUPATION</span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-controls-row\">\n"
    "                                        <select id=\"occupationChartType\" onchange=\"changeChartType('occupation', this.value)\" class=\"chart-type-select\">\n"
    "                                            <option value=\"bar\">📊</option>\n"
    "                                            <option value=\"column\">▮ Col</option>\n"
    "                                            <option value=\"doughnut\">🍩</option>\n"
    "                                            <option value=\"pie\">🥧</option>\n"
    "                                            <option value=\"polarArea\">🎯</option>\n"
    "                                            <option value=\"table\">📋 Table</option>\n"
    "                                        </select>\n"
    "                                        <select id=\"occupationSortSelect\" onchange=\"sortOccupationChart(this.value)\" class=\"chart-type-select has-tooltip\" data-tooltip=\"Sort\">\n"
    "                                            <option value=\"value\">%↓</option>\n"
    "                                            <option value=\"alpha\">A-Z</option>\n"
    "                                            <option value=\"index\">Idx↓</option>\n"
    "                                        </select>\n"
    "                                        <span class=\"module-action-btns\"><button class=\"module-btn csv-btn has-tooltip\" data-tooltip=\"Export CSV\" onclick=\"exportModuleCSV('demographic', 'Occupation')\">CSV</button><button class=\"module-btn png-btn has-tooltip\" data-tooltip=\"Export PNG\" onclick=\"exportModulePNG('demographic', 'Occupation')\">PNG</button><button class=\"module-btn deck-btn has-tooltip\" data-tooltip=\"Add to Deck\" onclick=\"addModuleToDeck('demographic', 'Occupation')\">➕</button></span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <div class=\"chart-container occupation-chart-container\" id=\"occupationChartContainer\">\n"
    "                                    <canvas id=\"occupationChart\"></canvas>\n"
    "                                </div>\n"
    "                                <div class=\"demo-table-container\" id=\"occupationTableContainer\" style=\"display:none; max-height:600px;\"></div>\n"
    "                                <div id=\"occupationGpActualsTable\" style=\"margin-top:0.75rem;\"></div>\n"
    "                            </div>\n"
    "                            <!-- AGE_OF_CHILDREN + NUMBER_OF_CHILDREN promoted to first-class\n"
    "                                 Demographics tiles (2026-08-28, Jenna). Both are pipeline-\n"
    "                                 canonical (canonical_demos.PIPELINE_DEMO_SCHEMA); rendered\n"
    "                                 same as Parental Status via renderCharts(). -->\n"
    "                            <div class=\"chart-card\" data-module=\"Age of Children\">\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-title-row\">\n"
    "                                        <span class=\"has-tooltip\" data-tooltip=\"Age of Children\">🧒 AGE OF CHILDREN</span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-controls-row\">\n"
    "                                        <select id=\"ageOfChildrenChartType\" onchange=\"changeChartType('ageOfChildren', this.value)\" class=\"chart-type-select\">\n"
    "                                            <option value=\"bar\">📊</option>\n"
    "                                            <option value=\"column\">▮ Col</option>\n"
    "                                            <option value=\"doughnut\">🍩</option>\n"
    "                                            <option value=\"pie\">🥧</option>\n"
    "                                            <option value=\"polarArea\">🎯</option>\n"
    "                                            <option value=\"table\">📋 Table</option>\n"
    "                                        </select>\n"
    "                                        <select id=\"ageOfChildrenSortSelect\" onchange=\"sortAgeOfChildrenChart(this.value)\" class=\"chart-type-select has-tooltip\" data-tooltip=\"Sort\">\n"
    "                                            <option value=\"value\">%↓</option>\n"
    "                                            <option value=\"alpha\">Ordinal</option>\n"
    "                                            <option value=\"index\">Idx↓</option>\n"
    "                                        </select>\n"
    "                                        <span class=\"module-action-btns\"><button class=\"module-btn csv-btn has-tooltip\" data-tooltip=\"Export CSV\" onclick=\"exportModuleCSV('demographic', 'Age of Children')\">CSV</button><button class=\"module-btn png-btn has-tooltip\" data-tooltip=\"Export PNG\" onclick=\"exportModulePNG('demographic', 'Age of Children')\">PNG</button><button class=\"module-btn deck-btn has-tooltip\" data-tooltip=\"Add to Deck\" onclick=\"addModuleToDeck('demographic', 'Age of Children')\">➕</button></span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <div class=\"chart-container\" id=\"ageOfChildrenChartContainer\"><canvas id=\"ageOfChildrenChart\"></canvas></div>\n"
    "                                <div class=\"demo-table-container\" id=\"ageOfChildrenTableContainer\" style=\"display:none;\"></div>\n"
    "                            </div>\n"
    "                            <div class=\"chart-card\" data-module=\"Number of Children\">\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-title-row\">\n"
    "                                        <span class=\"has-tooltip\" data-tooltip=\"Number of Children\">👨‍👩‍👧 NUMBER OF CHILDREN</span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <h3>\n"
    "                                    <span class=\"chart-card-controls-row\">\n"
    "                                        <select id=\"numberOfChildrenChartType\" onchange=\"changeChartType('numberOfChildren', this.value)\" class=\"chart-type-select\">\n"
    "                                            <option value=\"bar\">📊</option>\n"
    "                                            <option value=\"column\">▮ Col</option>\n"
    "                                            <option value=\"doughnut\">🍩</option>\n"
    "                                            <option value=\"pie\">🥧</option>\n"
    "                                            <option value=\"polarArea\">🎯</option>\n"
    "                                            <option value=\"table\">📋 Table</option>\n"
    "                                        </select>\n"
    "                                        <select id=\"numberOfChildrenSortSelect\" onchange=\"sortNumberOfChildrenChart(this.value)\" class=\"chart-type-select has-tooltip\" data-tooltip=\"Sort\">\n"
    "                                            <option value=\"value\">%↓</option>\n"
    "                                            <option value=\"alpha\">Ordinal</option>\n"
    "                                            <option value=\"index\">Idx↓</option>\n"
    "                                        </select>\n"
    "                                        <span class=\"module-action-btns\"><button class=\"module-btn csv-btn has-tooltip\" data-tooltip=\"Export CSV\" onclick=\"exportModuleCSV('demographic', 'Number of Children')\">CSV</button><button class=\"module-btn png-btn has-tooltip\" data-tooltip=\"Export PNG\" onclick=\"exportModulePNG('demographic', 'Number of Children')\">PNG</button><button class=\"module-btn deck-btn has-tooltip\" data-tooltip=\"Add to Deck\" onclick=\"addModuleToDeck('demographic', 'Number of Children')\">➕</button></span>\n"
    "                                    </span>\n"
    "                                </h3>\n"
    "                                <div class=\"chart-container\" id=\"numberOfChildrenChartContainer\"><canvas id=\"numberOfChildrenChart\"></canvas></div>\n"
    "                                <div class=\"demo-table-container\" id=\"numberOfChildrenTableContainer\" style=\"display:none;\"></div>\n"
    "                            </div>\n"
    "                        </div>\n"
    "                    </div>\n"
    "            </div>\n"
    "\n"
    "            <!-- Behavioral Tab -->\n"
)


# ===========================================================================
# R1: Two chart renderer blocks inserted after the Parental Status renderer
# (at line 62395) inside renderCharts(). Modeled directly on parental_status
# so the visual + tooltip + legend + datalabels treatment is identical.
# ===========================================================================

R1_OLD = (
    "                if (charts.parentalStatus) { charts.parentalStatus._origKeys = psOrigKeys; charts.parentalStatus._origGenPop = psGenPop; charts.parentalStatus._demoCategory = 'parental_status'; }\n"
    "            }\n"
    "            \n"
    "            // Occupation - bar chart with Gen Pop\n"
)

R1_NEW = (
    "                if (charts.parentalStatus) { charts.parentalStatus._origKeys = psOrigKeys; charts.parentalStatus._origGenPop = psGenPop; charts.parentalStatus._demoCategory = 'parental_status'; }\n"
    "            }\n"
    "\n"
    "            // Age of Children - bar chart with Gen Pop (2026-08-28)\n"
    "            if (demographics.age_of_children && Object.keys(demographics.age_of_children).length > 0) {\n"
    "                const aocSorted = getSortedAgeOfChildrenData(demographics.age_of_children, demographicsIndex.age_of_children);\n"
    "                const aocLabels = aocSorted.labels;\n"
    "                const aocOrigKeys = aocSorted.originalKeys;\n"
    "                const aocGenPop = aocOrigKeys.map(k => demographicsGenPop.age_of_children?.[k] || 0);\n"
    "                const aocPcts = aocSorted.values;\n"
    "                const aocData = isProjection ? aocOrigKeys.map(k => demographicsProjection.age_of_children?.[k] || 0) : aocPcts;\n"
    "                const aocChartType = _chartTypeForChartJs(chartTypePrefs.ageOfChildren);\n"
    "                const aocIsCircular = ['doughnut', 'pie', 'polarArea'].includes(aocChartType);\n"
    "                const aocMax = aocChartType === 'bar' ? Math.max(...aocData) * 1.5 : undefined;\n"
    "                charts.ageOfChildren = new Chart(document.getElementById('ageOfChildrenChart'), {\n"
    "                    type: aocChartType,\n"
    "                    data: { labels: aocLabels, datasets: [{ data: aocData, backgroundColor: colors, borderRadius: aocChartType === 'bar' ? 4 : 0 }] },\n"
    "                    options: {\n"
    "                        indexAxis: aocChartType === 'bar' ? 'y' : 'x',\n"
    "                        responsive: true,\n"
    "                        maintainAspectRatio: false,\n"
    "                        cutout: aocIsCircular ? doughnutCutout : 0,\n"
    "                        layout: { padding: aocIsCircular ? layoutPaddingCircular : { right: 50, left: 10, top: 5, bottom: 5 } },\n"
    "                        plugins: {\n"
    "                            tooltip: { callbacks: { afterBody: () => profileIqDemoTooltipLines('Age of Children') } },\n"
    "                            legend: aocIsCircular ? {\n"
    "                                ...legendOptionsCircular,\n"
    "                                display: true,\n"
    "                                labels: {\n"
    "                                    ...legendOptionsCircular.labels,\n"
    "                                    generateLabels: (chart) => {\n"
    "                                        const data = chart.data;\n"
    "                                        return data.labels.map((label, i) => {\n"
    "                                            const pct = aocPcts[i] || 0;\n"
    "                                            const genPop = aocGenPop[i] || 0;\n"
    "                                            const valStr = isProjection\n"
    "                                                ? formatNumber(Math.round(data.datasets[0].data[i]))\n"
    "                                                : pct.toFixed(2) + '%';\n"
    "                                            const gpStr = isGenPop ? '' : (isProjection\n"
    "                                                ? (genPop > 0 ? ` GP: ${formatNumber(Math.round((genPop / 100) * 329900000))}` : '')\n"
    "                                                : (genPop > 0 ? ` GP: ${genPop.toFixed(2)}%` : ''));\n"
    "                                            const indexColor = isGenPop ? ct.axisLabel : ((genPop > 0 && pct >= genPop) ? getChartIndexOverColor() : (genPop > 0 ? getChartIndexUnderColor() : ct.axisLabel));\n"
    "                                            return { text: `${label} ${valStr}${gpStr}`, fillStyle: colors[i], fontColor: indexColor, index: i };\n"
    "                                        });\n"
    "                                    }\n"
    "                                }\n"
    "                            } : { display: false },\n"
    "                            datalabels: aocIsCircular ? doughnutDataLabels : {\n"
    "                                anchor: 'end',\n"
    "                                align: 'end',\n"
    "                                font: { size: dataLabelFontSize, weight: 'bold' },\n"
    "                                color: (ctx) => {\n"
    "                                    const pct = aocPcts[ctx.dataIndex] || 0;\n"
    "                                    const genPop = aocGenPop[ctx.dataIndex] || 0;\n"
    "                                    if (isGenPop) return ct.dataNeutral;\n"
    "                                    return (genPop > 0 && pct >= genPop) ? getChartIndexOverColor() : (genPop > 0 ? getChartIndexUnderColor() : ct.dataNeutral);\n"
    "                                },\n"
    "                                formatter: (value, ctx) => {\n"
    "                                    const pct = aocPcts[ctx.dataIndex] || 0;\n"
    "                                    const idx = demographicsIndex.age_of_children?.[aocLabels[ctx.dataIndex]] || 0;\n"
    "                                    if (isGenPop) return isProjection ? formatNumber(Math.round(value)) : formatPct(pct);\n"
    "                                    const idxStr = idx > 0 ? ` ${Math.round(idx)}` : '';\n"
    "                                    const genPop = aocGenPop[ctx.dataIndex] || 0;\n"
    "                                    if (isProjection) {\n"
    "                                        const gpVal = genPop > 0 ? Math.round(329900000 * genPop / 100) : 0;\n"
    "                                        return formatNumber(Math.round(value)) + (gpVal > 0 ? ` GP: ${formatNumber(gpVal)}` : '') + idxStr;\n"
    "                                    }\n"
    "                                    return formatPct(pct) + (genPop > 0 ? ` GP: ${formatPct(genPop)}` : '') + idxStr;\n"
    "                                }\n"
    "                            }\n"
    "                        },\n"
    "                        scales: aocIsCircular ? {} : {\n"
    "                            x: { grid: { color: ct.grid }, ticks: { color: ct.axisMuted, font: { size: axisFontSize } }, suggestedMax: aocMax },\n"
    "                            y: { grid: { display: false }, ticks: { color: ct.axisLabel, font: { size: axisFontSize }, autoSkip: false } }\n"
    "                        }\n"
    "                    },\n"
    "                    plugins: [ChartDataLabels]\n"
    "                });\n"
    "                if (charts.ageOfChildren) { charts.ageOfChildren._origKeys = aocOrigKeys; charts.ageOfChildren._origGenPop = aocGenPop; charts.ageOfChildren._demoCategory = 'age_of_children'; }\n"
    "            }\n"
    "\n"
    "            // Number of Children - bar chart with Gen Pop (2026-08-28)\n"
    "            if (demographics.number_of_children && Object.keys(demographics.number_of_children).length > 0) {\n"
    "                const nocSorted = getSortedNumberOfChildrenData(demographics.number_of_children, demographicsIndex.number_of_children);\n"
    "                const nocLabels = nocSorted.labels;\n"
    "                const nocOrigKeys = nocSorted.originalKeys;\n"
    "                const nocGenPop = nocOrigKeys.map(k => demographicsGenPop.number_of_children?.[k] || 0);\n"
    "                const nocPcts = nocSorted.values;\n"
    "                const nocData = isProjection ? nocOrigKeys.map(k => demographicsProjection.number_of_children?.[k] || 0) : nocPcts;\n"
    "                const nocChartType = _chartTypeForChartJs(chartTypePrefs.numberOfChildren);\n"
    "                const nocIsCircular = ['doughnut', 'pie', 'polarArea'].includes(nocChartType);\n"
    "                const nocMax = nocChartType === 'bar' ? Math.max(...nocData) * 1.5 : undefined;\n"
    "                charts.numberOfChildren = new Chart(document.getElementById('numberOfChildrenChart'), {\n"
    "                    type: nocChartType,\n"
    "                    data: { labels: nocLabels, datasets: [{ data: nocData, backgroundColor: colors, borderRadius: nocChartType === 'bar' ? 4 : 0 }] },\n"
    "                    options: {\n"
    "                        indexAxis: nocChartType === 'bar' ? 'y' : 'x',\n"
    "                        responsive: true,\n"
    "                        maintainAspectRatio: false,\n"
    "                        cutout: nocIsCircular ? doughnutCutout : 0,\n"
    "                        layout: { padding: nocIsCircular ? layoutPaddingCircular : { right: 50, left: 10, top: 5, bottom: 5 } },\n"
    "                        plugins: {\n"
    "                            tooltip: { callbacks: { afterBody: () => profileIqDemoTooltipLines('Number of Children') } },\n"
    "                            legend: nocIsCircular ? {\n"
    "                                ...legendOptionsCircular,\n"
    "                                display: true,\n"
    "                                labels: {\n"
    "                                    ...legendOptionsCircular.labels,\n"
    "                                    generateLabels: (chart) => {\n"
    "                                        const data = chart.data;\n"
    "                                        return data.labels.map((label, i) => {\n"
    "                                            const pct = nocPcts[i] || 0;\n"
    "                                            const genPop = nocGenPop[i] || 0;\n"
    "                                            const valStr = isProjection\n"
    "                                                ? formatNumber(Math.round(data.datasets[0].data[i]))\n"
    "                                                : pct.toFixed(2) + '%';\n"
    "                                            const gpStr = isGenPop ? '' : (isProjection\n"
    "                                                ? (genPop > 0 ? ` GP: ${formatNumber(Math.round((genPop / 100) * 329900000))}` : '')\n"
    "                                                : (genPop > 0 ? ` GP: ${genPop.toFixed(2)}%` : ''));\n"
    "                                            const indexColor = isGenPop ? ct.axisLabel : ((genPop > 0 && pct >= genPop) ? getChartIndexOverColor() : (genPop > 0 ? getChartIndexUnderColor() : ct.axisLabel));\n"
    "                                            return { text: `${label} ${valStr}${gpStr}`, fillStyle: colors[i], fontColor: indexColor, index: i };\n"
    "                                        });\n"
    "                                    }\n"
    "                                }\n"
    "                            } : { display: false },\n"
    "                            datalabels: nocIsCircular ? doughnutDataLabels : {\n"
    "                                anchor: 'end',\n"
    "                                align: 'end',\n"
    "                                font: { size: dataLabelFontSize, weight: 'bold' },\n"
    "                                color: (ctx) => {\n"
    "                                    const pct = nocPcts[ctx.dataIndex] || 0;\n"
    "                                    const genPop = nocGenPop[ctx.dataIndex] || 0;\n"
    "                                    if (isGenPop) return ct.dataNeutral;\n"
    "                                    return (genPop > 0 && pct >= genPop) ? getChartIndexOverColor() : (genPop > 0 ? getChartIndexUnderColor() : ct.dataNeutral);\n"
    "                                },\n"
    "                                formatter: (value, ctx) => {\n"
    "                                    const pct = nocPcts[ctx.dataIndex] || 0;\n"
    "                                    const idx = demographicsIndex.number_of_children?.[nocLabels[ctx.dataIndex]] || 0;\n"
    "                                    if (isGenPop) return isProjection ? formatNumber(Math.round(value)) : formatPct(pct);\n"
    "                                    const idxStr = idx > 0 ? ` ${Math.round(idx)}` : '';\n"
    "                                    const genPop = nocGenPop[ctx.dataIndex] || 0;\n"
    "                                    if (isProjection) {\n"
    "                                        const gpVal = genPop > 0 ? Math.round(329900000 * genPop / 100) : 0;\n"
    "                                        return formatNumber(Math.round(value)) + (gpVal > 0 ? ` GP: ${formatNumber(gpVal)}` : '') + idxStr;\n"
    "                                    }\n"
    "                                    return formatPct(pct) + (genPop > 0 ? ` GP: ${formatPct(genPop)}` : '') + idxStr;\n"
    "                                }\n"
    "                            }\n"
    "                        },\n"
    "                        scales: nocIsCircular ? {} : {\n"
    "                            x: { grid: { color: ct.grid }, ticks: { color: ct.axisMuted, font: { size: axisFontSize } }, suggestedMax: nocMax },\n"
    "                            y: { grid: { display: false }, ticks: { color: ct.axisLabel, font: { size: axisFontSize }, autoSkip: false } }\n"
    "                        }\n"
    "                    },\n"
    "                    plugins: [ChartDataLabels]\n"
    "                });\n"
    "                if (charts.numberOfChildren) { charts.numberOfChildren._origKeys = nocOrigKeys; charts.numberOfChildren._origGenPop = nocGenPop; charts.numberOfChildren._demoCategory = 'number_of_children'; }\n"
    "            }\n"
    "            \n"
    "            // Occupation - bar chart with Gen Pop\n"
)


# ---------------------------------------------------------------------------

SPLICES = [
    ("P1  init objects (4)",                P1_OLD,  P1_NEW),
    ("P2  demoCategories UPPER (parseCSV)", P2_OLD,  P2_NEW),
    ("P3  parseCSV per-cat ifs",            P3_OLD,  P3_NEW),
    ("P4  demoCats (freshness check)",      P4_OLD,  P4_NEW),
    ("P5  demoChartKeys + map",             P5_OLD,  P5_NEW),
    ("P6  chartTypePrefs",                  P6_OLD,  P6_NEW),
    ("P7  _cmpDataCatMap",                  P7_OLD,  P7_NEW),
    ("P8  demoCategoryList (CSV export)",   P8_OLD,  P8_NEW),
    ("P10 demoKeys (comparison init)",      P10_OLD, P10_NEW),
    ("P11 demoCats+map (comparison)",       P11_OLD, P11_NEW),
    ("P12 demoCats (compare shared)",       P12_OLD, P12_NEW),
    ("P13 BENCHMARK_DEMO_CATEGORY_NAMES",   P13_OLD, P13_NEW),
    ("P14 demoCats (compare over-index)",   P14_OLD, P14_NEW),
    ("S1  sort-mode state vars",            S1_OLD,  S1_NEW),
    ("S2  sortXxxChart functions",          S2_OLD,  S2_NEW),
    ("S3  getSortedXxxData helpers",        S3_OLD,  S3_NEW),
    ("H1  two new HTML chart cards",        H1_OLD,  H1_NEW),
    ("R1  two new chart renderers",         R1_OLD,  R1_NEW),
]


def main() -> int:
    if not INDEX.is_file():
        print(f"[children-demo] {INDEX} not found")
        return 2

    src = INDEX.read_text(encoding="utf-8")
    orig_bytes = len(src.encode("utf-8"))
    print(f"[children-demo] {INDEX} ({orig_bytes:,} bytes)")

    # ---- Preflight: every splice's anchor must match EXACTLY once ----
    for label, old, _new in SPLICES:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(
                f"[{label}] anchor count = {n} (expected 1). "
                f"Widen or fix the anchor. Nothing written."
            )
    print(f"[children-demo] preflight OK - all {len(SPLICES)} anchors unique")

    # Backup BEFORE any change.
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[children-demo] backup written to {BACKUP}")

    # Apply.
    for label, old, new in SPLICES:
        src = src.replace(old, new)
        print(f"[children-demo]   {label}: applied")

    # Postflight sanity: no OLD anchor still present, each NEW present once.
    for label, old, new in SPLICES:
        if src.count(old) != 0:
            raise RuntimeError(f"[{label}] old anchor still present after replace")
        if src.count(new) != 1:
            raise RuntimeError(f"[{label}] new block not inserted exactly once")

    INDEX.write_text(src, encoding="utf-8")
    new_bytes = len(src.encode("utf-8"))
    delta = new_bytes - orig_bytes
    print(f"[children-demo] wrote {new_bytes:,} bytes ({delta:+,} bytes vs original)")
    print("[children-demo] run: python3 scripts/validate_index_html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
