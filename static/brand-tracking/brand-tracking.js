/* Brand Tracking: CNBC Pro competitive set. Window Jan 1 2026 to Sep 2 2026. */
(function () {
    'use strict';

    var WINDOW = 'Jan 1 2026 to Sep 2 2026';
    var CNBC_PAID = 487263;
    var CNBC_PRO = 243817;

    var MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug'];
    var MONTH_YM = [
        '2026-01', '2026-02', '2026-03', '2026-04',
        '2026-05', '2026-06', '2026-07', '2026-08'
    ];

    function series(paid, churn, retain) {
        return paid.map(function (p, i) {
            return {
                ym: MONTH_YM[i],
                label: MONTH_LABELS[i],
                paid: p,
                churn: churn[i],
                retain90: retain[i]
            };
        });
    }

    var BRANDS = [
        {
            id: 'cnbc-pro',
            name: 'CNBC Pro',
            type: 'Research / news DTC',
            account_label: 'Paid subscribers',
            paid: 243817,
            active_users: 243817,
            is_anchor: true,
            overlap_linear: 68.4,
            overlap_digital: 93.7,
            overlap_paid: 100.0,
            retention_90d: 72.4,
            churn_monthly: 4.4,
            retained_12m: 57.6,
            read: 'CNBC Pro is the paid research DTC comparison. 68.4% of Pro accounts also watch CNBC linear in window. Digital is near-total. Paid accounts stepped up in March (earnings) and June, then cooled in summer. Any CNBC paid product (Pro, CNBC+, Investing Club) is 487,263 unique accounts.',
            age: [
                ['18-24', 3.8], ['25-34', 14.7], ['35-44', 23.6],
                ['45-54', 27.4], ['55-64', 19.8], ['65+', 10.7]
            ],
            gender: [['Male', 68.4], ['Female', 31.1], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 2.7], ['$25,000-$49,999', 7.4],
                ['$50,000-$74,999', 12.8], ['$75,000-$99,999', 17.6],
                ['$100,000-$149,999', 25.3], ['$150,000+', 34.2]
            ],
            monthly: series(
                [228163, 229847, 232641, 233817, 234263, 238147, 236841, 237461],
                [4.6, 4.5, 4.2, 4.3, 4.4, 3.9, 4.6, 4.4],
                [71.3, 71.6, 72.8, 72.4, 72.1, 73.6, 71.8, 72.4]
            )
        },
        {
            id: 'tradingview',
            name: 'TradingView',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 1384217,
            active_users: 1384217,
            overlap_linear: 22.8,
            overlap_digital: 38.4,
            overlap_paid: 7.6,
            retention_90d: 77.3,
            churn_monthly: 3.6,
            retained_12m: 63.8,
            read: 'Largest paid charting set. Reads younger and more male than CNBC Pro. 7.6% of paid accounts also sit in a CNBC paid product. Charting habit holds: 77.3% still active at 90 days. Paid accounts climbed through the first half and were still adding in August.',
            age: [
                ['18-24', 13.8], ['25-34', 32.1], ['35-44', 26.7],
                ['45-54', 16.4], ['55-64', 8.1], ['65+', 2.9]
            ],
            gender: [['Male', 77.8], ['Female', 21.7], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 5.1], ['$25,000-$49,999', 12.4],
                ['$50,000-$74,999', 18.3], ['$75,000-$99,999', 20.8],
                ['$100,000-$149,999', 23.7], ['$150,000+', 19.7]
            ],
            monthly: series(
                [1087263, 1124817, 1168341, 1191263, 1214847, 1256173, 1271463, 1283641],
                [3.9, 3.8, 3.5, 3.6, 3.6, 3.3, 3.7, 3.6],
                [75.8, 76.1, 77.4, 77.1, 76.8, 78.2, 77.0, 77.3]
            )
        },
        {
            id: 'metatrader',
            name: 'MetaTrader',
            type: 'Trading platform',
            account_label: 'Platform accounts',
            paid: 612847,
            active_users: 612847,
            overlap_linear: 16.3,
            overlap_digital: 28.7,
            overlap_paid: 4.8,
            retention_90d: 81.8,
            churn_monthly: 2.0,
            retained_12m: 70.3,
            read: 'Largest non-media set. Broker-distributed terminal, not a news product. Linear CNBC overlap is the lowest among the large names. Sticky accounts, 2.0% monthly churn. US platform accounts still dwarf every research DTC in the set.',
            age: [
                ['18-24', 12.4], ['25-34', 28.7], ['35-44', 26.8],
                ['45-54', 18.3], ['55-64', 9.6], ['65+', 4.2]
            ],
            gender: [['Male', 81.6], ['Female', 17.9], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 6.4], ['$25,000-$49,999', 13.8],
                ['$50,000-$74,999', 19.3], ['$75,000-$99,999', 21.6],
                ['$100,000-$149,999', 22.1], ['$150,000+', 16.8]
            ],
            monthly: series(
                [548163, 551847, 558263, 561417, 564183, 571263, 573841, 576147],
                [2.2, 2.1, 1.9, 2.0, 2.0, 1.8, 2.1, 2.0],
                [80.6, 80.8, 81.7, 81.4, 81.3, 82.4, 81.6, 81.8]
            )
        },
        {
            id: 'seeking-alpha',
            name: 'Seeking Alpha',
            type: 'Research',
            account_label: 'Paid subscribers',
            paid: 198473,
            active_users: 198473,
            overlap_linear: 41.6,
            overlap_digital: 68.3,
            overlap_paid: 21.4,
            retention_90d: 73.6,
            churn_monthly: 4.6,
            retained_12m: 58.4,
            read: 'Closest research peer to CNBC Pro. Highest CNBC digital overlap in the research cluster (68.3%). 21.4% of Seeking Alpha paid also hold a CNBC paid product. Earnings months add accounts; summer gives some back.',
            age: [
                ['18-24', 5.4], ['25-34', 19.3], ['35-44', 26.8],
                ['45-54', 24.7], ['55-64', 16.1], ['65+', 7.7]
            ],
            gender: [['Male', 74.2], ['Female', 25.3], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 2.6], ['$25,000-$49,999', 7.1],
                ['$50,000-$74,999', 12.8], ['$75,000-$99,999', 17.4],
                ['$100,000-$149,999', 26.2], ['$150,000+', 33.9]
            ],
            monthly: series(
                [176841, 178263, 184147, 185463, 186817, 191263, 189417, 190643],
                [4.8, 4.7, 4.2, 4.4, 4.5, 4.1, 4.8, 4.6],
                [72.1, 72.4, 74.3, 73.8, 73.4, 75.1, 72.8, 73.6]
            )
        },
        {
            id: 'stockcharts',
            name: 'StockCharts.com',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 187263,
            active_users: 187263,
            overlap_linear: 31.7,
            overlap_digital: 44.2,
            overlap_paid: 12.6,
            retention_90d: 76.8,
            churn_monthly: 3.3,
            retained_12m: 61.7,
            read: 'Established US charting set, older than TradingView. Linear CNBC overlap 31.7%. Retention sits with the sticky charting cluster, not the news cluster. June added; July and August held most of it.',
            age: [
                ['18-24', 3.6], ['25-34', 13.8], ['35-44', 22.7],
                ['45-54', 28.1], ['55-64', 21.4], ['65+', 10.4]
            ],
            gender: [['Male', 76.8], ['Female', 22.7], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 3.4], ['$25,000-$49,999', 8.7],
                ['$50,000-$74,999', 14.8], ['$75,000-$99,999', 19.1],
                ['$100,000-$149,999', 25.2], ['$150,000+', 28.8]
            ],
            monthly: series(
                [168147, 169841, 172463, 173817, 174263, 178147, 176841, 177463],
                [3.5, 3.4, 3.1, 3.2, 3.3, 3.0, 3.4, 3.3],
                [75.4, 75.7, 77.1, 76.8, 76.4, 78.0, 76.2, 76.8]
            )
        },
        {
            id: 'marketwatch',
            name: 'MarketWatch',
            type: 'News',
            account_label: 'Paid subscribers',
            paid: 41827,
            active_users: 16847231,
            overlap_linear: 47.3,
            overlap_digital: 74.8,
            overlap_paid: 14.1,
            retention_90d: 62.4,
            churn_monthly: 7.2,
            retained_12m: 39.8,
            read: 'News, not a terminal. Paid DTC is the small slice; 16.8M active users is the real MarketWatch footprint. Highest CNBC digital overlap in the set (74.8%). Paid retention is weaker than charting. June news traffic lifted both paid and active users.',
            age: [
                ['18-24', 6.1], ['25-34', 17.4], ['35-44', 22.3],
                ['45-54', 24.1], ['55-64', 19.2], ['65+', 10.9]
            ],
            gender: [['Male', 63.8], ['Female', 35.7], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 4.7], ['$25,000-$49,999', 11.3],
                ['$50,000-$74,999', 16.4], ['$75,000-$99,999', 19.6],
                ['$100,000-$149,999', 23.4], ['$150,000+', 24.6]
            ],
            monthly: series(
                [36147, 36841, 38263, 38417, 38641, 40183, 39263, 39419],
                [7.6, 7.4, 6.8, 7.1, 7.2, 6.4, 7.5, 7.2],
                [60.3, 60.8, 63.7, 62.9, 62.4, 65.1, 61.6, 62.4]
            )
        },
        {
            id: 'tikr',
            name: 'TIKR',
            type: 'Research',
            account_label: 'Paid subscribers',
            paid: 81247,
            active_users: 81247,
            overlap_linear: 24.6,
            overlap_digital: 51.3,
            overlap_paid: 16.8,
            retention_90d: 71.8,
            churn_monthly: 5.1,
            retained_12m: 53.6,
            read: 'Filings-and-fundamentals research set. Younger than CNBC Pro, closer to Seeking Alpha on digital overlap. 16.8% also hold a CNBC paid product. Paid accounts rose through the first eight months with a March step-up.',
            age: [
                ['18-24', 8.3], ['25-34', 26.8], ['35-44', 28.4],
                ['45-54', 21.6], ['55-64', 11.2], ['65+', 3.7]
            ],
            gender: [['Male', 75.6], ['Female', 23.9], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 3.2], ['$25,000-$49,999', 8.6],
                ['$50,000-$74,999', 14.3], ['$75,000-$99,999', 19.4],
                ['$100,000-$149,999', 26.7], ['$150,000+', 27.8]
            ],
            monthly: series(
                [64183, 66847, 70263, 72147, 73841, 76263, 77417, 78641],
                [5.4, 5.3, 4.8, 5.0, 5.1, 4.7, 5.2, 5.1],
                [69.4, 69.8, 72.1, 71.6, 71.3, 73.4, 71.1, 71.8]
            )
        },
        {
            id: 'koyfin',
            name: 'Koyfin',
            type: 'Terminal',
            account_label: 'Paid subscribers',
            paid: 71483,
            active_users: 71483,
            overlap_linear: 33.4,
            overlap_digital: 56.7,
            overlap_paid: 26.3,
            retention_90d: 79.4,
            churn_monthly: 3.0,
            retained_12m: 65.8,
            read: 'Highest CNBC paid overlap outside CNBC Pro itself (26.3%). Terminal users look like Pro users: older, higher income, and they stay. 79.4% still active at 90 days. Advisor seats move slowly; the June lift was real but small.',
            age: [
                ['18-24', 3.1], ['25-34', 16.8], ['35-44', 28.3],
                ['45-54', 27.6], ['55-64', 17.4], ['65+', 6.8]
            ],
            gender: [['Male', 77.9], ['Female', 21.6], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 1.6], ['$25,000-$49,999', 4.8],
                ['$50,000-$74,999', 9.7], ['$75,000-$99,999', 15.8],
                ['$100,000-$149,999', 28.4], ['$150,000+', 39.7]
            ],
            monthly: series(
                [61847, 62463, 64183, 65147, 65841, 67263, 67417, 68149],
                [3.2, 3.1, 2.8, 2.9, 3.0, 2.7, 3.1, 3.0],
                [78.1, 78.4, 80.2, 79.6, 79.3, 81.1, 79.1, 79.4]
            )
        },
        {
            id: 'finviz',
            name: 'Finviz',
            type: 'Screening',
            account_label: 'Paid subscribers',
            paid: 39847,
            active_users: 39847,
            overlap_linear: 21.4,
            overlap_digital: 37.6,
            overlap_paid: 8.7,
            retention_90d: 70.1,
            churn_monthly: 5.4,
            retained_12m: 50.4,
            read: 'Screener-first. Paid Elite is a thin slice of a large free footprint. CNBC paid overlap 8.7%. Younger and more retail than the terminal cluster. March and June added Elite accounts; summer held.',
            age: [
                ['18-24', 12.4], ['25-34', 31.2], ['35-44', 26.1],
                ['45-54', 17.8], ['55-64', 9.1], ['65+', 3.4]
            ],
            gender: [['Male', 80.8], ['Female', 18.7], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 5.8], ['$25,000-$49,999', 13.4],
                ['$50,000-$74,999', 19.1], ['$75,000-$99,999', 21.3],
                ['$100,000-$149,999', 22.6], ['$150,000+', 17.8]
            ],
            monthly: series(
                [34163, 34841, 36147, 36263, 36417, 37841, 37263, 37641],
                [5.7, 5.6, 5.1, 5.3, 5.4, 4.9, 5.6, 5.4],
                [68.3, 68.7, 71.2, 70.6, 70.2, 72.4, 69.6, 70.1]
            )
        },
        {
            id: 'tc2000',
            name: 'TC2000',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 38417,
            active_users: 38417,
            overlap_linear: 36.1,
            overlap_digital: 41.8,
            overlap_paid: 13.2,
            retention_90d: 80.7,
            churn_monthly: 2.5,
            retained_12m: 67.3,
            read: 'US desktop charting, older than TradingView. Linear CNBC overlap 36.1%. Lowest churn in the charting cluster after MetaStock and Optuma. Accounts stay. Month-to-month paid barely moves.',
            age: [
                ['18-24', 1.8], ['25-34', 9.4], ['35-44', 21.7],
                ['45-54', 29.3], ['55-64', 24.7], ['65+', 13.1]
            ],
            gender: [['Male', 80.3], ['Female', 19.2], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 2.6], ['$25,000-$49,999', 7.4],
                ['$50,000-$74,999', 13.8], ['$75,000-$99,999', 18.6],
                ['$100,000-$149,999', 26.7], ['$150,000+', 30.9]
            ],
            monthly: series(
                [35147, 35263, 35841, 36147, 36263, 36817, 36641, 36849],
                [2.7, 2.6, 2.4, 2.5, 2.5, 2.3, 2.6, 2.5],
                [79.6, 79.8, 81.3, 80.8, 80.6, 82.1, 80.4, 80.7]
            )
        },
        {
            id: 'trendspider',
            name: 'TrendSpider',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 24183,
            active_users: 24183,
            overlap_linear: 19.7,
            overlap_digital: 36.4,
            overlap_paid: 7.4,
            retention_90d: 68.3,
            churn_monthly: 5.8,
            retained_12m: 47.1,
            read: 'Automated technicals, younger retail. CNBC paid overlap 7.4%. Churn sits above the sticky desktop charting names. Paid accounts added through spring and flattened in summer.',
            age: [
                ['18-24', 13.7], ['25-34', 33.1], ['35-44', 25.8],
                ['45-54', 16.4], ['55-64', 8.2], ['65+', 2.8]
            ],
            gender: [['Male', 79.6], ['Female', 19.9], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 5.6], ['$25,000-$49,999', 12.8],
                ['$50,000-$74,999', 18.4], ['$75,000-$99,999', 21.7],
                ['$100,000-$149,999', 23.9], ['$150,000+', 17.6]
            ],
            monthly: series(
                [19847, 20463, 21147, 21641, 21817, 22463, 22641, 22819],
                [6.1, 6.0, 5.5, 5.7, 5.8, 5.4, 5.9, 5.8],
                [66.4, 66.8, 69.1, 68.4, 68.1, 70.2, 67.7, 68.3]
            )
        },
        {
            id: 'fiscal-ai',
            name: 'Fiscal.ai',
            type: 'AI research',
            account_label: 'Paid subscribers',
            paid: 34817,
            active_users: 34817,
            overlap_linear: 17.3,
            overlap_digital: 46.8,
            overlap_paid: 11.7,
            retention_90d: 59.7,
            churn_monthly: 7.8,
            retained_12m: 37.6,
            read: 'Fastest-growing research set in window. Digital CNBC overlap 46.8% with thinner linear. Highest monthly churn in the group (7.8%). Paid accounts rose 77.5% from January to August. Early product, accounts still settling.',
            age: [
                ['18-24', 15.8], ['25-34', 35.2], ['35-44', 24.3],
                ['45-54', 14.6], ['55-64', 7.4], ['65+', 2.7]
            ],
            gender: [['Male', 72.4], ['Female', 27.1], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 4.3], ['$25,000-$49,999', 10.7],
                ['$50,000-$74,999', 16.4], ['$75,000-$99,999', 20.6],
                ['$100,000-$149,999', 25.1], ['$150,000+', 22.9]
            ],
            monthly: series(
                [18263, 20147, 23841, 25463, 27147, 29841, 31263, 32417],
                [8.4, 8.1, 7.4, 7.6, 7.7, 7.2, 7.9, 7.8],
                [54.6, 55.8, 59.3, 58.4, 58.1, 61.2, 58.7, 59.7]
            )
        },
        {
            id: 'ycharts',
            name: 'YCharts',
            type: 'Terminal',
            account_label: 'Paid subscribers',
            paid: 16847,
            active_users: 16847,
            overlap_linear: 37.2,
            overlap_digital: 58.4,
            overlap_paid: 24.8,
            retention_90d: 82.4,
            churn_monthly: 2.3,
            retained_12m: 70.8,
            read: 'Advisor terminal. Second-highest CNBC paid overlap (24.8%) and among the strongest 12-month retain (70.8%). Looks like Koyfin, smaller. Seat count barely moved. Accounts that are here stay.',
            age: [
                ['18-24', 1.6], ['25-34', 13.8], ['35-44', 27.4],
                ['45-54', 30.2], ['55-64', 19.1], ['65+', 7.9]
            ],
            gender: [['Male', 71.8], ['Female', 27.7], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 1.3], ['$25,000-$49,999', 3.8],
                ['$50,000-$74,999', 8.1], ['$75,000-$99,999', 14.2],
                ['$100,000-$149,999', 27.4], ['$150,000+', 45.2]
            ],
            monthly: series(
                [15263, 15417, 15641, 15817, 15841, 16147, 16263, 16417],
                [2.5, 2.4, 2.2, 2.3, 2.3, 2.1, 2.4, 2.3],
                [81.3, 81.6, 83.1, 82.6, 82.4, 83.8, 82.1, 82.4]
            )
        },
        {
            id: 'metastock',
            name: 'MetaStock',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 11263,
            active_users: 11263,
            overlap_linear: 38.6,
            overlap_digital: 40.3,
            overlap_paid: 14.6,
            retention_90d: 83.6,
            churn_monthly: 1.8,
            retained_12m: 73.4,
            read: 'Legacy desktop technicals. Oldest charting age mix. Linear CNBC overlap 38.6%. Lowest monthly churn in the set with Optuma (1.8%). Small, durable. Month-end paid drifted slightly down, then held.',
            age: [
                ['18-24', 1.2], ['25-34', 7.1], ['35-44', 18.3],
                ['45-54', 29.4], ['55-64', 28.2], ['65+', 15.8]
            ],
            gender: [['Male', 81.4], ['Female', 18.1], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 2.3], ['$25,000-$49,999', 6.8],
                ['$50,000-$74,999', 13.4], ['$75,000-$99,999', 19.1],
                ['$100,000-$149,999', 27.1], ['$150,000+', 31.3]
            ],
            monthly: series(
                [10847, 10817, 10841, 10763, 10741, 10817, 10763, 10741],
                [1.9, 1.9, 1.7, 1.8, 1.8, 1.6, 1.9, 1.8],
                [82.7, 82.8, 84.1, 83.6, 83.4, 84.6, 83.2, 83.6]
            )
        },
        {
            id: 'optuma',
            name: 'Optuma',
            type: 'Charting',
            account_label: 'Paid subscribers',
            paid: 6847,
            active_users: 6847,
            overlap_linear: 29.4,
            overlap_digital: 41.7,
            overlap_paid: 18.3,
            retention_90d: 84.7,
            churn_monthly: 1.7,
            retained_12m: 75.7,
            read: 'Smallest professional charting set. High retain, high income, 18.3% CNBC paid overlap. Not a volume play. Accounts that are here stay. Paid seats ticked up slowly from January through August.',
            age: [
                ['18-24', 1.0], ['25-34', 8.3], ['35-44', 22.1],
                ['45-54', 31.6], ['55-64', 25.2], ['65+', 11.8]
            ],
            gender: [['Male', 78.7], ['Female', 20.8], ['Other', 0.5]],
            income: [
                ['Less than $25,000', 1.1], ['$25,000-$49,999', 3.6],
                ['$50,000-$74,999', 8.2], ['$75,000-$99,999', 13.8],
                ['$100,000-$149,999', 27.6], ['$150,000+', 45.7]
            ],
            monthly: series(
                [6147, 6183, 6263, 6317, 6341, 6417, 6463, 6519],
                [1.8, 1.8, 1.6, 1.7, 1.7, 1.5, 1.8, 1.7],
                [83.8, 83.9, 85.2, 84.7, 84.6, 85.8, 84.4, 84.7]
            )
        }
    ];

    var state = { current: 'cnbc-pro' };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function num(n) {
        return Math.round(n).toLocaleString('en-US');
    }
    function pct(n) {
        return (Math.round(n * 10) / 10).toFixed(1) + '%';
    }
    function idx(paid) {
        return Math.round((paid / CNBC_PRO) * 1000) / 10;
    }
    function find(id) {
        for (var i = 0; i < BRANDS.length; i++) if (BRANDS[i].id === id) return BRANDS[i];
        return BRANDS[0];
    }
    function cnbcPro() { return find('cnbc-pro'); }
    function monthEnd(b) {
        return b.monthly[b.monthly.length - 1];
    }

    function hideOthers() {
        var ids = [
            'dashboardView', 'subscriberIQView', 'sfConversionView', 'rankerIQView',
            'ticketSalesIQView', 'ticketSalesTrackerView', 'hedgeFundIQView',
            'talentSearchIQView', 'talentTheaterIQView', 'formView', 'customAnalysisView',
            'roasIQView', 'ecommerceIQView', 'llmoIQView', 'sentimentIQView',
            'shareOfTimeIQView', 'flywheelConversionView', 'talentFitIQView',
            'journeyIQView', 'blueIQView', 'intentIQView', 'impactIQView',
            'helmIQView', 'trendsIQView', 'microdramasIQView'
        ];
        ids.forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
    }

    function renderRail() {
        var rail = document.getElementById('btiqBrandList');
        if (!rail) return;
        var html = '<div class="btiq-rail-label">Competitive set</div>';
        BRANDS.forEach(function (b) {
            var meta = esc(b.type) + ' · ' + num(b.paid);
            if (b.id === 'marketwatch') meta = 'News · ' + num(b.paid) + ' paid';
            html += '<button type="button" class="btiq-brand-btn'
                + (b.id === state.current ? ' active' : '')
                + (b.is_anchor ? ' anchor' : '')
                + '" data-id="' + esc(b.id) + '">'
                + '<span class="nm">' + esc(b.name) + '</span>'
                + '<span class="meta">' + meta + '</span>'
                + '</button>';
        });
        rail.innerHTML = html;
        rail.querySelectorAll('.btiq-brand-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                state.current = btn.getAttribute('data-id');
                renderAll();
            });
        });
    }

    function kpi(lbl, val, sub, accent) {
        return '<div class="btiq-kpi' + (accent ? ' accent' : '') + '">'
            + '<div class="lbl">' + esc(lbl) + '</div>'
            + '<div class="val">' + esc(val) + '</div>'
            + '<div class="sub">' + esc(sub) + '</div>'
            + '</div>';
    }

    function bar(label, value, cls) {
        var w = Math.max(0, Math.min(100, value));
        return '<div class="btiq-bar-row">'
            + '<div class="nm">' + esc(label) + '</div>'
            + '<div class="btiq-track"><div class="btiq-fill ' + (cls || '') + '" style="width:' + w + '%"></div></div>'
            + '<div class="pct">' + pct(value) + '</div>'
            + '</div>';
    }

    function demoCol(title, rows, compareRows) {
        var cmap = {};
        (compareRows || []).forEach(function (r) { cmap[r[0]] = r[1]; });
        var body = rows.map(function (r) {
            var c = cmap[r[0]];
            return '<div class="btiq-demo-row"><span>' + esc(r[0]) + '</span><span>'
                + pct(r[1]) + '</span><span>' + (c == null ? '' : pct(c)) + '</span></div>';
        }).join('');
        return '<div class="btiq-demo"><h4>' + esc(title) + '</h4>'
            + '<div class="btiq-demo-row"><span></span><span>This set</span><span>CNBC Pro</span></div>'
            + body + '</div>';
    }

    function renderHero(b) {
        var el = document.getElementById('btiqHero');
        if (!el) return;
        var extra = '';
        if (b.id === 'marketwatch') {
            extra = kpi('Active users', num(b.active_users), 'Digital MarketWatch users in window. Paid is the DTC slice.', false);
        }
        var aug = monthEnd(b);
        var vs = b.is_anchor
            ? 'CNBC Pro is the comparison base (index 100).'
            : 'Index ' + idx(b.paid) + ' vs CNBC Pro paid (' + num(CNBC_PRO) + ').';
        el.innerHTML = '<div class="btiq-card">'
            + '<h3>' + esc(b.name) + '</h3>'
            + '<p class="btiq-note">' + esc(b.type) + '. ' + WINDOW + '. '
            + (b.is_anchor ? 'DTC comparison product.' : 'Compared against CNBC Pro.')
            + ' Unique in-window accounts. August month-end active: ' + num(aug.paid) + '.</p>'
            + '<div class="btiq-kpis">'
            + kpi(b.account_label, num(b.paid), vs, true)
            + kpi('90-day retention', pct(b.retention_90d), 'Still active at day 90.')
            + kpi('Monthly churn', pct(b.churn_monthly), pct(b.retained_12m) + ' still active at month 12.')
            + extra
            + '</div>'
            + '<p class="btiq-read" style="margin-top:0.9rem;">' + esc(b.read) + '</p>'
            + '</div>';
    }

    function renderTrend(b) {
        var el = document.getElementById('btiqTrend');
        if (!el) return;
        var months = b.monthly;
        var maxPaid = 1;
        months.forEach(function (m) { if (m.paid > maxPaid) maxPaid = m.paid; });
        var jan = months[0].paid;
        var aug = months[months.length - 1].paid;
        var delta = aug - jan;
        var deltaPct = jan ? (delta / jan) * 100 : 0;
        var deltaTxt = (delta >= 0 ? '+' : '') + num(delta)
            + ' from January to August ('
            + (deltaPct >= 0 ? '+' : '') + (Math.round(deltaPct * 10) / 10).toFixed(1) + '%).';
        var cols = months.map(function (m) {
            var h = Math.max(8, Math.round((m.paid / maxPaid) * 72));
            return '<div class="btiq-month">'
                + '<div class="btiq-spark"><i style="height:' + h + 'px"></i></div>'
                + '<div class="mo">' + esc(m.label) + '</div>'
                + '<div class="pv">' + num(m.paid) + '</div>'
                + '<div class="ch">' + pct(m.churn) + ' churn</div>'
                + '</div>';
        }).join('');
        el.innerHTML = '<div class="btiq-card">'
            + '<h3>Paid accounts by month</h3>'
            + '<p class="btiq-note">Month-end active ' + esc(b.account_label.toLowerCase())
            + ', Jan 2026 through Aug 2026. The headline figure above is unique accounts in the full window, so it sits above any single month. '
            + deltaTxt + '</p>'
            + '<div class="btiq-months">' + cols + '</div></div>';
    }

    function renderOverlaps(b) {
        var el = document.getElementById('btiqOverlaps');
        if (!el) return;
        el.innerHTML = '<div class="btiq-card">'
            + '<h3>Overlap with CNBC</h3>'
            + '<p class="btiq-note">Share of this brand\'s ' + esc(b.account_label.toLowerCase())
            + ' who also appear in each CNBC cohort in window. CNBC paid covers any CNBC subscription product ('
            + num(CNBC_PAID) + ' unique paid accounts: Pro, CNBC+, Investing Club). CNBC Pro is '
            + num(CNBC_PRO) + ' of those.</p>'
            + '<div class="btiq-bars">'
            + bar('CNBC linear viewers', b.overlap_linear, '')
            + bar('CNBC digital users', b.overlap_digital, 'orchid')
            + bar('CNBC paid subscribers', b.overlap_paid, 'cobalt')
            + '</div></div>';
    }

    function renderDemos(b) {
        var el = document.getElementById('btiqDemos');
        if (!el) return;
        var p = cnbcPro();
        el.innerHTML = '<div class="btiq-card">'
            + '<h3>Subscriber profile</h3>'
            + '<p class="btiq-note">Audience mix on this brand vs CNBC Pro. Right column is CNBC Pro.</p>'
            + '<div class="btiq-demo-grid">'
            + demoCol('Age', b.age, p.age)
            + demoCol('Gender', b.gender, p.gender)
            + demoCol('Income', b.income, p.income)
            + '</div></div>';
    }

    function renderTable() {
        var el = document.getElementById('btiqTable');
        if (!el) return;
        var rows = BRANDS.slice().sort(function (a, b) { return b.paid - a.paid; });
        var body = rows.map(function (b) {
            var cls = 'clickable'
                + (b.id === state.current ? ' active' : '')
                + (b.is_anchor ? ' anchor' : '');
            var jan = b.monthly[0].paid;
            var aug = b.monthly[b.monthly.length - 1].paid;
            var chg = jan ? ((aug - jan) / jan) * 100 : 0;
            return '<tr class="' + cls + '" data-id="' + esc(b.id) + '">'
                + '<td>' + esc(b.name) + '</td>'
                + '<td>' + esc(b.type) + '</td>'
                + '<td class="num">' + num(b.paid) + '</td>'
                + '<td class="num">' + num(aug) + '</td>'
                + '<td class="num">' + (chg >= 0 ? '+' : '') + (Math.round(chg * 10) / 10).toFixed(1) + '%</td>'
                + '<td class="num">' + idx(b.paid) + '</td>'
                + '<td class="num">' + pct(b.overlap_linear) + '</td>'
                + '<td class="num">' + pct(b.overlap_digital) + '</td>'
                + '<td class="num">' + pct(b.overlap_paid) + '</td>'
                + '<td class="num">' + pct(b.retention_90d) + '</td>'
                + '<td class="num">' + pct(b.churn_monthly) + '</td>'
                + '</tr>';
        }).join('');
        el.innerHTML = '<div class="btiq-card">'
            + '<h3>Full set</h3>'
            + '<p class="btiq-note">Unique paid accounts in window, ranked. Aug is month-end active. Jan-Aug is the change in month-end active. Overlap columns are share of that brand also in the CNBC cohort. Index 100 = CNBC Pro unique paid.</p>'
            + '<div class="btiq-table-wrap"><table class="btiq-table"><thead><tr>'
            + '<th>Brand</th><th>Type</th><th>In-window unique</th><th>Aug active</th><th>Jan-Aug</th><th>Index vs Pro</th>'
            + '<th>CNBC linear</th><th>CNBC digital</th><th>CNBC paid</th>'
            + '<th>90-day retain</th><th>Monthly churn</th>'
            + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
        el.querySelectorAll('tr.clickable').forEach(function (tr) {
            tr.addEventListener('click', function () {
                state.current = tr.getAttribute('data-id');
                renderAll();
            });
        });
    }

    function renderAll() {
        var b = find(state.current);
        renderRail();
        renderHero(b);
        renderTrend(b);
        renderOverlaps(b);
        renderDemos(b);
        renderTable();
    }

    window.showBrandTrackingIQ = function () {
        var dd = document.getElementById('viewNavDropdown');
        var opt = dd && dd.querySelector('option[value="brandTrackingIQ"]');
        if (opt && opt.disabled) return;
        if (typeof setViewNavDropdown === 'function') setViewNavDropdown('brandTrackingIQ');
        hideOthers();
        var view = document.getElementById('brandTrackingIQView');
        if (view) {
            view.style.display = 'flex';
            view.style.visibility = 'visible';
        }
        renderAll();
    };
})();
