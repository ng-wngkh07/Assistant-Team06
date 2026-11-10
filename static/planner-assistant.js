/**
 * ARYM — Weekly Lesson Topic Classifier, 1h30p (1 Subject/Day) Auto-Scheduler
 * & Calendar PDF Bundle Frontend Module (`static/planner-assistant.js`)
 *
 * Single-responsibility module wiring:
 * 1. `#form-weekly-lesson` -> `POST /api/timetable/planner/weekly-lessons`
 *    (auto-classifies topic + generates attached PDF exercise worksheet with answer key at the end)
 * 2. `#btn-auto-schedule-90m` -> `POST /api/timetable/planner/auto-schedule-courses`
 *    (allocates 90-minute self-study sessions in free timetable windows, strictly 1 subject/day,
 *     and attaches the generated PDF worksheet to each session)
 * 3. `#btn-export-calendar-bundle` -> `GET /api/timetable/export-bundle.zip`
 *    (downloads `.zip` containing `.ics` with RFC 5545 `URL`/`ATTACH`/`DESCRIPTION` + all PDF files)
 */
(function () {
  'use strict';

  function esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function parseDMYToIso(raw) {
    const val = String(raw || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(val)) return val;
    const m = val.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
    if (!m) return null;
    const dd = String(m[1]).padStart(2, '0');
    const mm = String(m[2]).padStart(2, '0');
    const yyyy = m[3];
    return `${yyyy}-${mm}-${dd}`;
  }

  async function loadWeeklyLessons() {
    const tbody = document.getElementById('weekly-lessons-table-body');
    if (!tbody) return;
    try {
      const res = await fetch('/api/timetable/planner/weekly-lessons');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return;
      renderWeeklyLessons(data.lessons || []);
    } catch (_err) {
      // Ignore transient network errors on initial load
    }
  }

  function renderWeeklyLessons(lessons) {
    const tbody = document.getElementById('weekly-lessons-table-body');
    if (!tbody) return;
    if (!Array.isArray(lessons) || !lessons.length) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center;" class="text-muted">
            Chưa có bài học tuần nào. Hãy nhập bài sẽ học hoặc đã học để hệ thống tự động phân loại chủ đề và đính kèm PDF bài tập vào lịch.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = '';
    for (const item of lessons) {
      const tr = document.createElement('tr');
      const statusBadge = item.status === 'studied'
        ? '<span class="sched-badge badge-study-done">✅ Đã học trên lớp</span>'
        : '<span class="sched-badge badge-window">📖 Sẽ học trong tuần</span>';

      const pdfCellHtml = item.pdf_url
        ? `<a href="${esc(item.pdf_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline" style="padding: 4px 10px; font-size: 11.5px;">📄 Tải PDF (${Number(item.question_count || 4)} câu + Đáp án)</a>`
        : '<span class="text-muted">Đang tạo...</span>';

      tr.innerHTML = `
        <td><strong>${esc(item.course)}</strong></td>
        <td>${esc(item.lesson_title)}</td>
        <td>
          <span class="sched-badge badge-study-ai">🏷️ ${esc(item.classified_topic)}</span>
          ${item.domain_label ? `<br><small class="text-muted">${esc(item.domain_label)}</small>` : ''}
        </td>
        <td>${statusBadge}</td>
        <td>${pdfCellHtml}</td>
        <td style="text-align: center;">
          <button type="button" class="btn btn-sm btn-outline btn-del-weekly-lesson" data-id="${Number(item.id)}" style="padding: 2px 8px;" title="Xóa bài học">&times;</button>
        </td>
      `;

      tr.querySelector('.btn-del-weekly-lesson')?.addEventListener('click', async () => {
        await fetch(`/api/timetable/planner/weekly-lessons/${Number(item.id)}`, { method: 'DELETE' });
        await loadWeeklyLessons();
      });

      tbody.appendChild(tr);
    }
  }

  async function handleAddWeeklyLesson(e) {
    e.preventDefault();
    const courseInput = document.getElementById('weekly-lesson-course');
    const titleInput = document.getElementById('weekly-lesson-title');
    const statusSelect = document.getElementById('weekly-lesson-status');
    const countInput = document.getElementById('weekly-lesson-count');
    const msgEl = document.getElementById('weekly-lesson-status-msg');
    const submitBtn = document.getElementById('btn-add-weekly-lesson');

    const course = (courseInput?.value || '').trim();
    const lesson_title = (titleInput?.value || '').trim();
    const status = statusSelect?.value || 'upcoming';
    const question_count = Math.max(1, Math.min(10, parseInt(countInput?.value || '4', 10) || 4));

    if (!course || !lesson_title) {
      if (msgEl) msgEl.textContent = '⚠️ Vui lòng nhập đầy đủ môn học và tên bài học trong tuần.';
      return;
    }

    if (submitBtn) submitBtn.disabled = true;
    if (msgEl) {
      msgEl.textContent = `Đang phân loại chủ đề cho bài «${lesson_title}» và khởi tạo file PDF bài tập kèm đáp án cuối trang…`;
    }

    try {
      const res = await fetch('/api/timetable/planner/weekly-lessons', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ course, lesson_title, status, question_count }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const lesson = data.lesson || {};
      if (titleInput) titleInput.value = '';
      if (msgEl) {
        msgEl.innerHTML = `✅ Đã phân loại bài học vào chủ đề <strong>${esc(lesson.classified_topic)}</strong> (${esc(lesson.domain_label)}) và tạo sẵn <a href="${esc(lesson.pdf_url)}" target="_blank" rel="noopener noreferrer" style="color: #ffffff; text-decoration: underline;">📄 File PDF bài tập kèm đáp án cuối trang</a>.`;
      }
      await loadWeeklyLessons();
    } catch (err) {
      if (msgEl) msgEl.textContent = `❌ ${err.message || 'Không lưu được bài học tuần.'}`;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleAutoSchedule90m() {
    const btn = document.getElementById('btn-auto-schedule-90m');
    const msgEl = document.getElementById('weekly-lesson-status-msg');
    const explainBox = document.getElementById('planner-explanation-box');
    const explainText = document.getElementById('planner-explanation-text');

    if (btn) btn.disabled = true;
    if (msgEl) {
      msgEl.textContent = 'Đang phân tích thời gian rảnh từ Thời khóa biểu, phân bổ lịch tự học 1 tiếng 30 phút (1 môn/ngày) và đính kèm PDF bài tập…';
    }

    try {
      const res = await fetch('/api/timetable/planner/auto-schedule-courses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_minutes: 90 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }

      if (msgEl) {
        msgEl.textContent = `✅ ${data.explanation || `Đã xếp ${data.scheduled_count || 0} buổi tự học (1h30p · 1 môn/ngày) kèm PDF bài tập.`}`;
      }
      if (explainBox && explainText) {
        explainBox.style.display = 'block';
        explainText.textContent = data.explanation || '';
      }

      await loadWeeklyLessons();
      window.dispatchEvent(new CustomEvent('arym:planner-refresh'));
    } catch (err) {
      if (msgEl) msgEl.textContent = `⚠️ ${err.message || 'Không thể tự động phân bổ lịch học.'}`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function handleExportCalendarBundleZip() {
    const startInput = document.getElementById('export-start-date');
    const endInput = document.getElementById('export-end-date');
    const reminderSelect = document.getElementById('export-reminder');
    const statusEl = document.getElementById('export-status');
    const btn = document.getElementById('btn-export-calendar-bundle');

    const startIso = parseDMYToIso(startInput?.value) || new Date().toISOString().slice(0, 10);
    const endDefault = new Date(Date.now() + 112 * 86400000).toISOString().slice(0, 10);
    const endIso = parseDMYToIso(endInput?.value) || endDefault;
    const reminder = parseInt(reminderSelect?.value || '60', 10) || 0;

    if (btn) btn.disabled = true;
    if (statusEl) {
      statusEl.textContent = 'Đang đóng gói tệp .zip gồm lịch .ics và toàn bộ file PDF bài tập (có đáp án cuối trang)…';
    }

    try {
      const params = new URLSearchParams({
        start_date: startIso,
        end_date: endIso,
        reminder_minutes: String(reminder),
      });
      const res = await fetch(`/api/timetable/export-bundle.zip?${params.toString()}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `arym-lich-hoc-va-bai-tap-pdf-${startIso}.zip`;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 250);
      if (statusEl) {
        statusEl.textContent = '✅ Đã tải gói .zip gồm tệp lịch .ics (tích hợp liên kết & đính kèm PDF) cùng toàn bộ file PDF bài tập.';
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `❌ ${err.message || 'Không xuất được gói .zip.'}`;
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function init() {
    document.getElementById('form-weekly-lesson')?.addEventListener('submit', handleAddWeeklyLesson);
    document.getElementById('btn-auto-schedule-90m')?.addEventListener('click', handleAutoSchedule90m);
    document.getElementById('btn-export-calendar-bundle')?.addEventListener('click', handleExportCalendarBundleZip);
    window.addEventListener('study:account-changed', loadWeeklyLessons);
    loadWeeklyLessons();
  }

  window.ARYMPlannerAssistant = {
    loadWeeklyLessons,
    renderWeeklyLessons,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
