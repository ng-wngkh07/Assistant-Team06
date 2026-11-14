/**
 * ARYM — Topic-Based Exercise PDF Export Module (`static/practice-pdf.js`)
 *
 * Single-responsibility frontend module that exports topic-based questions or
 * extracted exercises into a formatted PDF file where:
 * - Part I contains the questions/exercises without inline answers.
 * - Part II (at the end of the PDF) contains the answer key, explanations, and
 *   verbatim source citations for easy self-checking.
 */
(function () {
  'use strict';

  function triggerBlobDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 250);
  }

  async function downloadPdfFromPost(payload, statusEl) {
    if (statusEl) {
      statusEl.textContent = `Đang tạo file PDF bài tập cho chủ đề «${payload.topic}» (kèm bảng đáp án đối chiếu cuối trang)…`;
    }
    const res = await fetch('/api/learning/export-pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Không thể xuất PDF (HTTP ${res.status})`);
    }
    const blob = await res.blob();
    const safeSlug = String(payload.topic || 'bai-tap')
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'chude';
    triggerBlobDownload(blob, `arym-bai-tap-${safeSlug}.pdf`);
    if (statusEl) {
      statusEl.textContent = `✅ Đã xuất file PDF bài tập chủ đề «${payload.topic}» kèm đáp án đối chiếu ở cuối trang.`;
    }
  }

  async function handleExportPracticeTopicPdf() {
    const topicInput = document.getElementById('learning-topic');
    const difficultySelect = document.getElementById('learning-difficulty');
    const statusEl = document.getElementById('learning-generate-status');
    const btnEl = document.getElementById('btn-export-practice-pdf');

    const session = window.ARYMPracticeState?.getSession?.() || null;
    const topic = (topicInput?.value || session?.topic || '').trim();
    if (!topic) {
      if (statusEl) {
        statusEl.textContent = 'Vui lòng nhập hoặc chọn chủ đề trước khi xuất file PDF.';
      }
      topicInput?.focus();
      return;
    }

    const difficulty = difficultySelect?.value || session?.difficulty || 'medium';
    if (btnEl) btnEl.disabled = true;
    try {
      await downloadPdfFromPost(
        {
          topic,
          difficulty,
          practice_id: session?.practice_id || null,
        },
        statusEl
      );
    } catch (err) {
      if (statusEl) statusEl.textContent = err.message || 'Không xuất được PDF.';
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  async function handleExportDialogPdf() {
    const session = window.ARYMPracticeState?.getSession?.() || null;
    const subtitleEl = document.getElementById('practice-dialog-subtitle');
    const btnEl = document.getElementById('btn-dialog-export-pdf');
    const topic = (session?.topic || subtitleEl?.textContent || 'Chủ đề luyện tập').replace(/^Chủ đề:\s*/i, '').trim();

    if (btnEl) btnEl.disabled = true;
    try {
      await downloadPdfFromPost(
        {
          topic: topic || 'Chủ đề luyện tập',
          difficulty: session?.difficulty || 'medium',
          practice_id: session?.practice_id || null,
        },
        subtitleEl
      );
    } catch (err) {
      if (subtitleEl) subtitleEl.textContent = err.message || 'Không xuất được PDF.';
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  async function handleExportExercisesPdf() {
    const topicInput = document.getElementById('exercise-topic');
    const statusEl = document.getElementById('learning-exercise-status');
    const btnEl = document.getElementById('btn-export-exercises-pdf');
    const topic = (topicInput?.value || '').trim();
    if (!topic) {
      if (statusEl) {
        statusEl.textContent = 'Vui lòng nhập chủ đề bài tập trước khi xuất PDF.';
      }
      topicInput?.focus();
      return;
    }

    if (btnEl) btnEl.disabled = true;
    try {
      await downloadPdfFromPost(
        {
          topic,
          difficulty: 'medium',
        },
        statusEl
      );
    } catch (err) {
      if (statusEl) statusEl.textContent = err.message || 'Không xuất được PDF.';
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  function init() {
    document.getElementById('btn-export-practice-pdf')?.addEventListener('click', handleExportPracticeTopicPdf);
    document.getElementById('btn-dialog-export-pdf')?.addEventListener('click', handleExportDialogPdf);
    document.getElementById('btn-export-exercises-pdf')?.addEventListener('click', handleExportExercisesPdf);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
