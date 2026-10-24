# Timetable recognition, review and calendar export

A recognized timetable is an editable draft. Export uses only the confirmed schedule in the local timetable store. OCR or VLM output never becomes a confirmed timetable automatically.

## User workflow

1. Upload a PNG, JPEG or WebP timetable image. Use a clear original rather than a compressed thumbnail. The public [14-course image](../tests/fixtures/timetables/multisubject.png) is safe to use for debugging.
2. Compare every draft row against the original image: course name, weekday, start/end time and room. Check the row count and distinct course names, including wrapped names and repeated meetings. Retain unknown values until they can be read or supplied by the user.
3. If the source lists lesson periods rather than clock times, enter verified clock times or explicitly apply the configured period mapping. Do not infer a clock time from an unfamiliar institution's lesson numbers.
4. Resolve missing fields and overlaps. Choose whether to replace the existing confirmed schedule or add to it. Review that choice before confirming so a new semester does not silently mix with an old schedule.
5. Confirm and inspect the saved weekly calendar. Unconfirmed edits are not included in exports.
6. Enter the semester dates using the DD/MM/YYYY picker. Export the `.ics` file, then inspect its course names, dates, times and rooms in the destination calendar before using it as your schedule. The dates sent to the API use YYYY-MM-DD.

A new image is identified by its content hash, not its filename. Recognition caches also bind the extraction version and model/engine identity. Reusing a cached extraction still creates a fresh editable draft. Correcting a draft does not rewrite the original image or claim the recognition model learned that correction.

## Incomplete images and learning examples

An incomplete screenshot is a useful example for learning when to request more information. Preserve the original image and readable values. Label the missing weekday, clock times, full course text or invalid time range, together with the expected notification asking for another image or a manual correction. Keep a registration-window screenshot separately labelled as `not_class_timetable`: administrative dates are not class meetings.

The draft response includes a `data_sufficiency` assessment and warnings even when the recognition model supplies no warnings. These warnings survive cache reuse and draft reload. `visible_rows_have_required_fields` describes the extracted rows only; it does not verify every visible block, cropped content, an entire week or semester recurrence. Compare the draft against the source before confirming it. The assessment cannot detect every omission or plausible but incorrect model value.

Training candidates must retain their image hash, observed fields, explicit missing fields, expected user notification, review state and split assignment. Use `insufficient_information` supervision to teach abstention and clarification, while positive schedule labels contain only source-supported values. Keep augmented crops and near-duplicate images in the same split to avoid leakage. Review the missing-information label against the image rather than treating the model's own warning as ground truth. These examples remain in the local review queue until source review, dataset, model compatibility and memory gates permit training. Saving a candidate does not execute training or activate an adapter. Text-only fine-tuning does not establish image-reading ability.

## Processing and boundaries

The dense registered-table path reads literal OCR cell positions on supported hosts. On Mac, other layouts use locally loaded MLX vision weights; text-only inference cannot read an image. Both paths retain the review step. The native image worker must bound image size and prefill work to avoid exhausting the GPU, and reject truncated responses. No hosted model service is permitted. The extracted subject must come from its own row/cell, rather than a schema example or the previous semester. Neither a successful JSON response nor a cache hit proves that all subjects were recognized.

The backend validates confirmation data and rejects conflicting or incomplete entries. Export rejects invalid rows as a whole rather than silently dropping subjects or assigning an absent weekday to Monday. It uses the confirmed course names, clock times, rooms and ISO weekday (1=Monday, 7=Sunday). For Vietnamese labels, Thứ 2 is ISO Monday; an unlabelled model integer is insufficient evidence for that conversion.

The calendar follows [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545): local DTSTART/DTEND use Asia/Ho_Chi_Minh, recurring UNTIL is expressed in UTC, and the semester's final local day is included without admitting the following day. Text is escaped and long UTF-8 lines are folded. The exporter emits weekly event definitions, so 14 course/meeting rows across two complete weeks produce 14 VEVENT definitions and 28 occurrences. Unusual rotating weeks, cancellations and individual date exceptions require explicit support and review; they must not be guessed from a weekly image.

## Reproducible debugging

Use an isolated timetable database and image directory. The [public fixture guide](../tests/fixtures/EXTENDED_CORPUS.md) supplies a matching PNG and JSON with 14 distinct fictional courses across all seven weekdays. The test suite checks the image-payload API through draft, cache, user edit, replacement confirmation and export with a controlled recognition response; this checks transport and persistence, not live OCR accuracy. Live recognition requires a separate original-image/content comparison.

When reporting a defect, record the source image hash, extraction method/version, model digest if used, row counts at draft and confirmed stages, edited values, chosen replace/add behavior, semester range and exported event/occurrence counts. Share only synthetic or permitted data. An export that opens successfully can still have wrong contents, so compare identities and dates as well as file syntax.
