"""Select the isolated title renderer when a gameplay-only source is merged."""

INTRO_RENDER_NAME = 'Title Intro Render'


def intro_render_target(project):
    targets = {t['name']: t for t in project['targets']}
    return targets.get(INTRO_RENDER_NAME, targets.get('Render'))
