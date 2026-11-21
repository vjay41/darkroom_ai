# Lightroom editing principles (researched 21 Nov 2026)

Learned from online research after the DSC_2411-1 edit was judged worse than the original.
Sources at the bottom. These principles OVERRIDE the old mechanical "numbers -> sliders" habit.

## 0. The one rule: the photo wins, not the edit
- A good photo often needs almost nothing. If the before looks better than the after, the fix is LESS, not more.
- Editing enhances what is already in the photo; it cannot rescue a bad one. Never sledgehammer.
- Decide the INTENT before touching a slider: what should the viewer notice? What must be preserved
  (mood, warmth, story, skin believability)? Every slider is judged against that intent.

## 1. The nine sins (check every edit against these)
1. Alien portraits - over skin softening (plastic look)
2. Sharpness halos - over-sharpening amplifies noise
3. Saturation overload - skin turns orange; use Vibrance, not Saturation
4. Magic presets - preset worship over fundamentals
5. Black-hole vignettes - visible edge = too far
6. Sledgehammer editing - over-processing makes the EDIT the subject
7. Color clipping - colors pushed past their limits go flat
8. Clarity gone wild - grey, crunchy skin and halos
9. The inverse sin: doing nothing when a real problem exists (clipped faces, wrong WB)

## 2. Tone: top-to-bottom, one job per slider (Adobe official + camerahow)
Roles (each control owns a different histogram region):
| Slider | Owns | Note |
|---|---|---|
| Exposure | midtones (30-70%), overall key | set for the SUBJECT (faces), not the histogram |
| Contrast | midtone shape | LAST in the tone pass; keep it small |
| Highlights | 70-90% region | negative recovers blown bright areas; cannot bring back clipped pixels |
| Shadows | 10-30% region | lift until texture reads; stop before the image goes flat/grey |
| Whites | 90-100% endpoint | sets the white-clipping point |
| Blacks | 0-10% endpoint | sets the black-clipping point |

Order: Exposure -> Highlights -> Shadows -> Whites -> Blacks -> (then) Contrast -> presence/color.
- Highlights and Shadows tend to work best as a roughly balanced pair (Adobe's guidance). Lopsided
  pairs shift the histogram and interact with every other control - the image gets muddy and you
  chase sliders. Choose the size from what you see in the preview, not from a remembered value.
- Contrast AFTER the regional work: it reshapes midtones; using it to compensate for a sky
  or shadow problem changes the rest of the frame unnecessarily.
- A small amount of clipping is FINE (speculars, small white details). Clipping across
  important subject detail (faces, fabric texture) is not.

## 3. White balance is an artistic choice, not a correction (camerahow, presets.io)
- Warm light in a warm scene (ceremony, family room, sunset, diya candles) is usually the STORY.
  Neutralising it kills the photo. Correct a cast only when something you KNOW is neutral
  (white shirt, grey wall) looks wrong, or skin looks orange/green/blue.
- A JPEG's displayed Temp value is NOT a Kelvin measurement of the scene light - judge by eye
  on faces and known neutrals, never by chasing a number.
- Mixed lighting: one balance cannot neutralise the whole frame. Favor the subject and a
  believable sense of the original light over a forced correction.
- Believable skin reads red > green > blue. Judge it by eye on the faces; a small hue nudge of the
  orange range toward red warms skin. Do not chase a reference number.

## 4. Color and presence: optional finishing, not mandatory
- Vibrance before saturation (vibrance spares already-saturated colors and protects skin).
- Clarity/Dehaze only when you can name what they improve (texture, haze); both are easy to overdose.
- If a saturation increase makes the background exciting but the skin less believable, back it off.

## 5. The verification loop (this is what the old workflow skipped)
1. analyze_photo -> but its numbers are EVIDENCE for a decision, not a recipe.
   color_cast "warm" on a warm scene is NOT a defect. flat mid-tones is only a problem if the
   subject reads flat.
2. One pass = one small idea (recover highlights. add a touch of depth. nudge skin hue).
   Apply -> get_preview at the SAME long edge -> look at the faces and the important detail
   BEFORE the next slider. 2-3 rounds max, then show Vijay.
3. Judge against the BEFORE: is the subject better? Is the mood intact? If the after is
   merely different, or worse - undo and try less. "Undo + do less" is a valid, good result.
4. Report honestly: what you changed, what you deliberately did NOT change, measured before/after
   (median, shadow p05, clip %), and the undo route.

## 6. Mapping to the darkroom_ai tool surface
| Principle | Tool |
|---|---|
| Exposure for the subject | adjust_lighting.exposure (EV, relative) - often 0 |
| Symmetric H/S pair | adjust_lighting.highlights/shadows (relative -100..100) |
| Endpoints | adjust_lighting.whites/blacks (rarely needed; small) |
| Midtone shape, last | adjust_lighting.contrast (small) |
| WB by eye, minimal | adjust_white_balance.temperature/tint (JPEG: relative -100..100; +temp = warmer) |
| Skin/hue nudges | adjust_hsl (orange hue toward red; luminance for skin lightness) |
| Take it back | undo (scope last|all, this session only) |

## 7. CASE STUDY - DSC_2411-1 (21 Nov 2026), edit judged WORSE than original
Scene: outdoor Hindu wedding ceremony, bright daylight, warm palette (red carpet, gold,
garlands), 4 people. analyze_photo said: flat mid-tones (median 0.41), 2.1% clip, warm cast.
What was applied: six sliders at once (exposure up, contrast up, highlights down, shadows up, a cooler
temperature and a tint nudge).
Result: the median moved past mid (washed), clipping got worse (2.1% -> 3.9%), the warm ceremonial
mood was removed, overall flatter and less inviting. Vijay: "the before version is better."
Violations: #0 (no intent - chased numbers), #2 (lopsided H/S + contrast in one pass),
#3 (neutralised mood warmth that was the story), #6 (sledgehammer: six sliders at once).
What the principles would have done: name the intent (keep the warm ceremony, recover the clipped
bright areas, keep depth), make ONE small highlights/shadows pass, leave exposure and white balance
alone, then check the faces before anything else.

## Sources
- Adobe HelpX, "Tone Control Adjustment Procedure" (official slider roles, tonal regions,
  symmetric H/S behaviour, Alt-drag endpoint setting)
- camerahow.com, "Mastering Lightroom Basic Post Processing Workflow" (top-to-bottom order,
  JPEG WB caveat, subject-first exposure, vibrance vs saturation, before/after compare)
- presets.io, "Ultimate Guide to Skin Tone Editing in Lightroom" (WB ranges, HSL orange,
  R>G>B skin rule, local tools)
- nancysmiley.com, "Lightroom Editing Sins! Avoid These 9 Deadly Mistakes" (the nine sins)
- SLRLounge / vfpresets, "Lightroom Develop Module Basics" (work top-to-bottom, heavy lifting
  in Highlights/Shadows, vibrance first)
- Adobe Community, "Internal Order of Adjustments for RAW development" (engine pipeline order:
  WB -> exposure/contrast -> H/S -> ... -> curves -> grading; slider-move order is irrelevant)
