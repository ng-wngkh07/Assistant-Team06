# Study planning

Study planning combines a class timetable, personal commitments, deadlines and editable study sessions. Image extraction is an input method inside this workflow. A timetable-to-calendar converter alone is an occasional convenience; a weekly plan can support repeated use by helping students decide what to study, when to study and what remains unfinished. This is a product judgment, not measured user demand.

The feature is useful as a separate study tool when it protects fixed commitments, makes insufficient time visible, lets the student approve changes and exports an actionable calendar. Product validation should measure time to first usable plan, unassisted completion, corrections required and weekly reuse. Retention and learning benefit have not been established by software tests.

## Workflow

1. **Class timetable:** upload an image, check extraction and confirm it, or enter classes manually. Unconfirmed extraction cannot silently become the saved calendar.
2. **Personal time:** add work, extra classes, sport, rest and available study windows. Select the planning week. Advanced daily limits are optional. Continuing saves validated inputs before moving on.
3. **Plan and deadlines:** inspect the combined week, enter dated deadlines and time estimates, generate proposed study sessions, edit them and confirm. Completed, locked or manual sessions saved in SQLite retain their existing protection strictly from stored records rather than client-supplied draft flags. For any dated planning week, newly generated deadline tasks, weekly lesson sessions and rescheduled sessions start at or after the current `Asia/Ho_Chi_Minh` time rounded up to the next whole minute; historical weeks and exhausted current-week windows leave affected items in `unscheduled_tasks` or `unplaced_courses` with explicit warnings instead of allocating slots in the past, and confirming newly proposed or edited past sessions fails with HTTP `409 Conflict`. Planner CRUD and preference updates propagate an explicit `db_path` so account-scoped calls never touch the default database. The scheduling algorithm uses constraints; the label does not imply a language model generated the plan.
4. **Export:** choose an inclusive date range and a deadline reminder lead time, then download an iCalendar file. Classes and recurring personal activities repeat within that range. Confirmed study sessions belong only to the selected planning week. Deadline events do not repeat. Undated legacy deadlines must be given a date before export.

The file uses Asia/Ho_Chi_Minh time and RFC 5545 DISPLAY alarms. A reminder lead time of zero disables alarms. Calendar applications control import behavior, notification permissions and delivery. Import is a snapshot, not two-way synchronization; use a separate destination calendar or remove an older import before importing a revised file to avoid duplicates. Verify the deadline time and alarm in the destination application. Successful file generation cannot certify that a device displayed a notification.

Accounts save that a first-use guide has been shown per feature on the server. Returning accounts skip previously shown guides across devices using the same server, and the help button reopens them. The dismiss button closes the current banner. Local guest guide history stays in that browser. Account-specific planner data, history and original uploads are separated from the local guest workspace; existing guest data is not automatically transferred into a new account.

## References and design decisions

- [Todoist getting started](https://www.todoist.com/help/todoist/get-started/get-started-with-todoist-OgNNJR): distinguish tasks, time allocation and deadlines; keep the first plan small enough to act on.
- [Nextcloud Calendar](https://github.com/nextcloud/calendar): use reviewable calendar events and reminders as the workflow destination. This implementation does not copy its licensed application code.
- [Driver.js configuration](https://driverjs.com/docs/configuration): keep one active guide and allow replay. The application uses a small native banner without an additional tour dependency.
- [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545): event, timezone and alarm interchange rules.
- [Python icalendar](https://github.com/collective/icalendar): independent parsing of exported files during verification.
- [Google Calendar import](https://support.google.com/calendar/answer/37118?hl=en) and [Outlook import or subscribe](https://support.microsoft.com/en-us/outlook/import-or-subscribe-to-a-calendar-in-outlook-com-or-outlook-on-the-web): file import differs from a subscribed live calendar.
