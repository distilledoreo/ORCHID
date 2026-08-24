# Accessibility

Phase A targets keyboard- and screen-reader-operable application chrome while
recognizing that pixel editing itself needs deliberate alternatives.

## Requirements for changed UI

- Use semantic HTML and native controls before ARIA.
- Give icon-only controls accessible names and expose toggle/disabled state.
- Support keyboard operation without a pointer and keep focus visibly apparent.
- Move focus predictably for dialogs and restore it on close.
- Do not communicate state by color alone; respect reduced motion.
- Meet WCAG 2.2 AA contrast targets for text and essential controls.
- Announce consequential changes and errors without flooding live regions.
- Keep browser zoom and text scaling usable; do not block platform shortcuts.

Canvas content needs an accessible name and a textual summary of document and
selection state. The active pixel-or-mask paint target is exposed through native
radio controls and a live textual canvas description. Tests should cover names,
roles, tab order, and critical keyboard flows. The Layers, Channels, and History
tabs expose tab/tabpanel relationships, roving focus, and Arrow/Home/End
navigation. Free Transform uses real 28px handle buttons with stable accessible
names, arrow-key scale/rotation, Shift modifiers, Escape cancellation, live
status, and numeric matrix fields as a precision alternative. Crop has a named
tool button, a pointer-drawn preview, four labeled numeric fields as a
pointer-independent alternative, explicit Apply and Cancel controls, live
result/cancellation announcements, and Enter/Escape equivalents. Phase A is not
yet a declaration of WCAG conformance. Canvas Size and Image Size use labeled
native modal dialogs, autofocus the width field, report validation errors with
`role="alert"`, support form submission, close on Escape, and restore focus to
their trigger. Canvas Size exposes all nine anchors as named radio controls;
Image Size exposes a named proportions checkbox and resampling selector. Their
shortcuts are Ctrl/Command+Alt+C and Ctrl/Command+Alt+I respectively. Reveal All
is a named command button and announces its result or no-op. Manual
assistive-technology testing and a published audit are still required for a
conformance claim.

Save document and Open are named native buttons. Ctrl/Command+S saves the
editable `.freetoshop` format and Ctrl/Command+O opens the local file chooser;
completion, cancellation, checksum failures, and compatibility errors are
announced through the existing live toast region.

Ellipse, Freehand Lasso, and Magic Wand are named toggle buttons. M selects the
rectangular tool, Shift+M toggles rectangle/ellipse, L selects Lasso, and W
selects Magic Wand when focus is outside an input. Native controls expose
selection mode, 0–255 Wand tolerance, contiguous matching, refinement radius,
and all five refinement commands. Pointer-drawn ellipse and lasso gestures do
not yet have a keyboard geometry editor, so they are not a complete non-pointer
alternative.

The Apply Filter modal autofocuses its named filter selector, exposes native
controls for the active filter parameters, describes destructive application,
reports invalid combinations with `role="alert"`, and provides explicit Apply
and Cancel actions. Its live canvas preview is visual; exact pixel values are not
currently announced. Escape cancellation and trigger-focus restoration use the
shared native dialog behavior.
