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

      let exActionHtml = '';
      if (item.practice_id || item.exercise_status === 'available') {
        exActionHtml = `<button type="button" class="btn btn-sm btn-outline btn-open-lesson-practice" data-practice-id="${esc(item.practice_id)}" style="padding: 3px 8px; font-size: 11px; margin-right: 4px;" title="Mở bộ câu hỏi luyện tập">📝 Bài tập</button>`;
      } else if (item.exercise_status === 'unavailable') {
        exActionHtml = `<button type="button" class="btn btn-sm btn-outline btn-retry-lesson-practice" data-id="${Number(item.id)}" style="padding: 3px 8px; font-size: 11px; margin-right: 4px;" title="Thử tạo lại bài tập từ tài liệu nguồn">🔄 Thử lại</button>`;
      } else {
        exActionHtml = `<button type="button" class="btn btn-sm btn-outline btn-gen-lesson-practice" data-id="${Number(item.id)}" style="padding: 3px 8px; font-size: 11px; margin-right: 4px;" title="Tạo bài tập tự động từ tài liệu học tập">＋ Tạo bài tập</button>`;
      }

      const pdfCellHtml = item.pdf_url
        ? `<div style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">${exActionHtml}<a href="${esc(item.pdf_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline" style="padding: 3px 8px; font-size: 11px;">📄 Tải PDF (${Number(item.question_count || 4)} câu + Đáp án)</a></div>`
        : `<div style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">${exActionHtml}${item.exercise_status === 'unavailable' ? '<span class="text-muted" style="font-size: 11px;">Chưa có bài tập nguồn</span>' : ''}</div>`;

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

      tr.querySelector('.btn-open-lesson-practice')?.addEventListener('click', () => {
        const topic = item.lesson_title || item.classified_topic || item.course;
        document.dispatchEvent(new CustomEvent('app-open-practice-session', {
          detail: { practice_id: item.practice_id, topic: topic }
        }));
      });

      const handleGen = async (btn) => {
        btn.disabled = true;
        const oldText = btn.textContent;
        btn.textContent = '⏳ Đang tạo...';
        try {
          const res = await fetch(`/api/timetable/planner/weekly-lessons/${Number(item.id)}/exercise/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            alert(data.detail || 'Không thể tạo bài tập cho bài học này.');
            await loadWeeklyLessons();
            return;
          }
          await loadWeeklyLessons();
          if (data.practice) {
            document.dispatchEvent(new CustomEvent('app-open-practice-session', {
              detail: { practiceSession: data.practice, topic: item.lesson_title || item.classified_topic || item.course }
            }));
          }
        } catch (err) {
          alert(`Lỗi kết nối: ${err.message}`);
          btn.disabled = false;
          btn.textContent = oldText;
        }
      };

      tr.querySelector('.btn-retry-lesson-practice')?.addEventListener('click', (e) => handleGen(e.currentTarget));
      tr.querySelector('.btn-gen-lesson-practice')?.addEventListener('click', (e) => handleGen(e.currentTarget));

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
      msgEl.textContent = `Đang phân loại chủ đề cho bài «${lesson_title}» và tìm kiếm bài tập từ tài liệu nguồn…`;
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
        if (lesson.pdf_url) {
          msgEl.innerHTML = `✅ Đã phân loại bài học vào chủ đề <strong>${esc(lesson.classified_topic)}</strong> (${esc(lesson.domain_label)}) và chuẩn bị sẵn <a href="${esc(lesson.pdf_url)}" target="_blank" rel="noopener noreferrer" style="color: #ffffff; text-decoration: underline;">📄 File PDF bài tập</a>.`;
        } else {
          msgEl.innerHTML = `✅ Đã lưu bài học theo chủ đề <strong>${esc(lesson.classified_topic)}</strong> (${esc(lesson.domain_label)}) (chưa có bài tập từ tài liệu nguồn).`;
        }
      }
      await loadWeeklyLessons();
    } catch (err) {
      if (msgEl) msgEl.textContent = `❌ ${err.message || 'Không lưu được bài học tuần.'}`;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function handleAutoSchedule90m() {
    const primaryBtn = document.getElementById('btn-generate-study-plan');
    if (primaryBtn) {
      primaryBtn.click();
      return;
    }

    const btn = document.getElementById('btn-auto-schedule-90m');
    const msgEl = document.getElementById('weekly-lesson-status-msg');
    const explainBox = document.getElementById('planner-explanation-box');
    const explainText = document.getElementById('planner-explanation-text');
    const prefMinutesEl = document.getElementById('pref-session-minutes');
    const sessionMinutes = prefMinutesEl && parseInt(prefMinutesEl.value, 10) > 0 ? parseInt(prefMinutesEl.value, 10) : 60;

    if (btn) btn.disabled = true;
    if (msgEl) {
      msgEl.textContent = 'Đang phân tích thời gian rảnh và chuẩn bị đề xuất lịch học…';
    }

    try {
      const res = await fetch('/api/timetable/planner/auto-schedule-courses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_minutes: sessionMinutes, propose_only: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }

      if (explainBox && explainText) {
        explainBox.style.display = 'block';
        explainText.textContent = data.explanation || '';
      }

      // Preview draft before writing to DB
      if (typeof window.StudyPlanner?.renderPlanDraft === 'function') {
        window.StudyPlanner.renderPlanDraft(data);
      } else {
        window.dispatchEvent(new CustomEvent('arym:preview-plan-draft', { detail: data }));
      }

      if (msgEl) {
        if ((data.scheduled_count || 0) > 0) {
          msgEl.textContent = `📋 Đã lập đề xuất ${data.scheduled_count} buổi tự học. Hãy kiểm tra các khung giờ đề xuất bên dưới và bấm "Xác nhận lịch".`;
        } else {
          msgEl.textContent = `⚠️ Chưa tìm thấy khung giờ phù hợp: ${data.explanation || 'Hãy kiểm tra lại thời gian rảnh trong tuần.'}`;
        }
      }

      const draftCard = document.getElementById('planner-draft-card');
      if (draftCard) {
        draftCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      await loadWeeklyLessons();
    } catch (err) {
      if (msgEl) msgEl.textContent = `⚠️ ${err.message || 'Không thể tạo đề xuất lịch học.'}`;
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
      statusEl.textContent = 'Đang đóng gói tệp .zip gồm lịch .ics và các tệp PDF bài tập có sẵn…';
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
