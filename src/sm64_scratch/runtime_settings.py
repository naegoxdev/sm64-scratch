"""Preserve the VM scheduler configuration required by the title renderer."""

def ensure_intro_runtime_settings(project):
    """Recover the title's scheduler setting if an older pruner removed it.

    TurboWarp stores project options in a floating stage comment. It is a
    runtime dependency even though it has no blockId. The title's explicit
    30-Hz clock runs on the original 60-Hz VM scheduler; these rates differ.
    Preserve existing project options rather than overwriting user settings.
    """
    stage = next(t for t in project["targets"] if t.get("isStage"))
    comments = stage.setdefault("comments", {})
    for comment in comments.values():
        if comment.get("blockId") is None and "// _twconfig_" in comment.get("text", ""):
            return False
    comments["intro_runtime_settings"] = {
        "blockId": None, "x": 50, "y": 50, "width": 350, "height": 170,
        "minimized": True,
        "text": 'Configuration for https://turbowarp.org/\n'
                'The VM scheduler runs at 60 Hz; the intro animation clock runs at 30 Hz.\n'
                '{"framerate":60} // _twconfig_'}
    return True
