"""StreamScout client scope on the build spec (Jess + Spike 2026-10-16).

The interpret draft records what the CLIENT said about where the
audience watches or listens; this stamps it onto the spec the worker
reads:

  content_seed_platforms_named  the platforms the user named (the
                                build is strict on them and refuses
                                rather than substitute another
                                platform's seeds)
  youtube_include_clips         False = full episodes only (default),
                                True = the channel's clips too
  youtube_limit                 the fair-survey cap (0 = every video)

Only consumption universes (a seed_source on the spec) carry these.
"""


def stamp_client_scope(spec, draft, scrub, subject):
    """Mutates and returns ``spec``. ``scrub`` is the chat's field scrubber."""
    if not isinstance(spec, dict) or not isinstance(draft, dict) or not spec.get('seed_source'):
        return spec
    named = draft.get('named_platforms')
    if isinstance(named, list):
        spec['content_seed_platforms_named'] = [
            scrub(str(v), field='named_platforms', subject=subject, max_len=40, single_line=True)
            for v in named if str(v).strip()][:6]
    if isinstance(draft.get('youtube_include_clips'), bool):
        spec['youtube_include_clips'] = bool(draft['youtube_include_clips'])
    lim = draft.get('youtube_limit')
    if isinstance(lim, (int, float)) and not isinstance(lim, bool) and 0 <= lim <= 5000:
        spec['youtube_limit'] = int(lim)
    return spec
