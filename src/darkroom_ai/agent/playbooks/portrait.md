# Portrait playbook

Goal: natural skin, eyes readable, no blown highlights on faces.

Exposure and tone decisions come from the MEASUREMENTS. Your own impression of how bright the
preview is, or whether it clips, is unreliable (in testing the model called a balanced photo
"bright" and missed clipped highlights). If the preview and the numbers disagree, the numbers win.
Tone values are on a 0 to 1 scale: `p50` is the median brightness, `p05` and `p95` the dark and
bright ends; `highlights_pct` and `shadows_pct` are the clipped share of the frame in percent.

1. Read the numbers first: `p50`, `p05`, `p95`, `highlights_pct`, `shadows_pct`, the colour cast.
2. Exposure: raise it only if `p50` is below about 0.40, lower it only if `p50` is above about
   0.65, in small steps (0.2 to 0.5 EV per pass). Between those values leave exposure alone
   unless the user explicitly asks for brighter or darker; then use one small step (about 0.3 EV).
3. Highlights: lower them (10 to 30) if `highlights_pct` is above about 1. Check the per-channel
   values too: a high `red_high_pct` on a portrait usually means skin is clipping.
4. Shadows: lift them (10 to 30) if `shadows_pct` is above about 1 or `p05` is below about 0.03.
5. White balance: correct a cast only if the measured cast says there is one (not "no noticeable
   cast") and its confidence is "ok". Small steps (5 to 15). The preview may be used to judge
   whether skin looks natural afterwards.
6. After each pass read the NEW MEASUREMENTS and judge the change by them. Stop when the numbers
   are inside the ranges above; it is fine to stop after one pass.
7. Do not crop and do not replace the background unless the user asked for it.
