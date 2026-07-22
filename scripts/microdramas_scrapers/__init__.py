"""
Microdramas IQ scrapers package.

Each scraper writes a normalized daily snapshot to S3 that gets merged
into the persistent catalog by microdramas_iq.integrate_snapshot().

Snapshot shape (see microdramas_iq.integrate_snapshot):
    {
      "source":     "peacock",
      "fetched_at": ISO8601,
      "titles": [
        { "title", "series", "poster_url", "deep_link",
          "rank", "surface", "episodes" }
      ]
    }
"""
