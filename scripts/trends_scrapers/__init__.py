# Trends IQ scraper package.
#
# Every module in this package is a standalone daily snapshot writer for one
# external trending source. run_all.py orchestrates them behind a single
# Hetzner crontab entry; each script also runs standalone for debugging.
