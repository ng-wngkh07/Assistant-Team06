/**
 * ARYM — Button Spotlight Onboarding Tour Module (`static/onboarding-tour.js`)
 *
 * Single-responsibility module providing:
 * 1. A small Liquid Glass banner wrapping around each key UI button (`.arym-tour-button-banner`)
 * 2. An SVG directional arrow pointing from the button banner to a separate callout frame (`.arym-tour-arrow-svg`)
 * 3. A dedicated callout frame explaining the button's role (`.arym-tour-callout-frame`)
 * 4. Strict per-account persistence: automatically displays ONLY ONCE on a newly created account
 *    and never shows a second time unless manually replayed via `#btn-replay-tour`.
 */
(function () {
  'use strict';

  const TOUR_STEPS = [
    {
      selector: '#mode-qa',
      badge: 'Nút chế độ 1',
      title: 'Hỏi đáp học tập có trích dẫn nguồn',
      description: 'Chuyển sang không gian đặt câu hỏi trực tiếp. Mọi câu trả lời đều bám sát giáo trình đã nạp kèm số trang và đoạn trích nguyên văn.',
    },
    {
      selector: '#mode-tools',
      badge: 'Nút chế độ 2',
      title: 'Công cụ học tập & Xuất PDF bài tập',
      description: 'Tạo câu hỏi trắc nghiệm theo chủ đề, xuất file PDF bài tập kèm bảng đáp án đối chiếu riêng ở cuối trang và ôn tập ngắt quãng (SRS).',
    },
    {
      selector: '#mode-timetable',
      badge: 'Nút chế độ 3',
      title: 'Thời khóa biểu & Tự động xếp lịch 1h30p',
      description: 'Tải ảnh/nhập lịch học, tự động tìm giờ trống xếp lịch tự học 1 tiếng 30 phút (1 môn/ngày), phân loại bài học tuần và đính kèm PDF bài tập.',
    },
    {
      selector: '#btn-open-sidebar',
      badge: 'Ngăn Kính Nổi',
      title: 'Mở Ngăn Kính Lịch Sử & Thư Viện',
      description: 'Mở tấm kính lỏng trượt nổi bên trái để quản lý các phiên trò chuyện, xem thư viện tài liệu cá nhân và kiểm tra trạng thái mô hình.',
    },
    {
      selector: '#btn-clear-chat',
      badge: 'Phiên mới',
      title: 'Tạo cuộc trò chuyện mới',
      description: 'Khởi tạo một phiên hỏi đáp độc lập để lưu lịch sử trao đổi theo từng môn học hoặc chủ đề ôn tập.',
    },
    {
      selector: '#btn-chat-attach',
      badge: 'Tài liệu',
      title: 'Đính kèm giáo trình & tệp PDF/Ảnh',
      description: 'Tải lên PDF bài giảng, tài liệu môn học hoặc ảnh chụp trang sách để hệ thống trích xuất và trả lời có căn cứ.',
    },
    {
      selector: '#processing-level',
      badge: 'Chế độ suy luận',
      title: 'Chọn mức độ xử lý câu trả lời',
      description: 'Chuyển đổi giữa Thường (nhanh), Suy luận (phân tích từng bước) hoặc Suy luận sâu cho các bài toán phức tạp.',
    },
  ];

  let overlayEl = null;
  let bannerEl = null;
  let badgeEl = null;
  let arrowSvgEl = null;
  let arrowPathEl = null;
  let calloutEl = null;
  let currentStepIndex = 0;
  let activeCompletionCallback = null;
  let resizeHandler = null;

  function ensureOverlayElements() {
    if (overlayEl && document.body.contains(overlayEl)) {
      return;
    }
    overlayEl = document.createElement('div');
    overlayEl.className = 'arym-tour-overlay';
    overlayEl.id = 'arym-tour-overlay';
    overlayEl.setAttribute('role', 'dialog');
    overlayEl.setAttribute('aria-modal', 'false');
    overlayEl.setAttribute('aria-label', 'Hướng dẫn nút chức năng ARYM');

    overlayEl.innerHTML = `
      <div class="arym-tour-backdrop" id="arym-tour-backdrop"></div>
      <div class="arym-tour-button-banner" id="arym-tour-button-banner">
        <span class="arym-tour-button-badge" id="arym-tour-button-badge">Nút chức năng</span>
      </div>
      <svg class="arym-tour-arrow-svg" id="arym-tour-arrow-svg" aria-hidden="true">
        <defs>
          <marker id="arym-tour-arrowhead" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" class="arym-tour-arrow-head"></path>
          </marker>
        </defs>
        <path id="arym-tour-arrow-path" class="arym-tour-arrow-path" marker-end="url(#arym-tour-arrowhead)" d="M0,0 L0,0"></path>
      </svg>
      <div class="arym-tour-callout-frame" id="arym-tour-callout-frame">
        <div class="arym-tour-callout-header">
          <span class="arym-tour-step-counter" id="arym-tour-step-counter">1 / ${TOUR_STEPS.length}</span>
          <span class="arym-tour-target-chip" id="arym-tour-target-chip"></span>
        </div>
        <h3 class="arym-tour-callout-title" id="arym-tour-callout-title"></h3>
        <p class="arym-tour-callout-desc" id="arym-tour-callout-desc"></p>
        <div class="arym-tour-callout-footer">
          <button type="button" class="arym-tour-btn" id="arym-tour-skip">Đã hiểu · Đóng</button>
          <div class="arym-tour-nav-group">
            <button type="button" class="arym-tour-btn" id="arym-tour-prev">← Trước</button>
            <button type="button" class="arym-tour-btn is-primary" id="arym-tour-next">Tiếp theo →</button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(overlayEl);
    bannerEl = overlayEl.querySelector('#arym-tour-button-banner');
    badgeEl = overlayEl.querySelector('#arym-tour-button-badge');
    arrowSvgEl = overlayEl.querySelector('#arym-tour-arrow-svg');
    arrowPathEl = overlayEl.querySelector('#arym-tour-arrow-path');
    calloutEl = overlayEl.querySelector('#arym-tour-callout-frame');

    overlayEl.querySelector('#arym-tour-skip').addEventListener('click', () => finishTour(true));
    overlayEl.querySelector('#arym-tour-backdrop').addEventListener('click', () => finishTour(true));
    overlayEl.querySelector('#arym-tour-prev').addEventListener('click', () => {
      if (currentStepIndex > 0) {
        currentStepIndex -= 1;
        renderCurrentStep();
      }
    });
    overlayEl.querySelector('#arym-tour-next').addEventListener('click', () => {
      if (currentStepIndex < TOUR_STEPS.length - 1) {
        currentStepIndex += 1;
        renderCurrentStep();
      } else {
        finishTour(true);
      }
    });
  }

  function findAvailableSteps() {
    return TOUR_STEPS.filter((step) => {
      const el = document.querySelector(step.selector);
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    });
  }

  function positionBannerAndCallout(targetEl, step, stepIdx, totalSteps) {
    const rect = targetEl.getBoundingClientRect();
    const pad = 6;
    const bannerTop = Math.max(6, rect.top - pad);
    const bannerLeft = Math.max(6, rect.left - pad);
    const bannerWidth = rect.width + pad * 2;
    const bannerHeight = rect.height + pad * 2;

    bannerEl.style.top = `${bannerTop}px`;
    bannerEl.style.left = `${bannerLeft}px`;
    bannerEl.style.width = `${bannerWidth}px`;
    bannerEl.style.height = `${bannerHeight}px`;
    badgeEl.textContent = step.badge || 'Nút';

    overlayEl.querySelector('#arym-tour-step-counter').textContent = `Bước ${stepIdx + 1} / ${totalSteps}`;
    overlayEl.querySelector('#arym-tour-target-chip').textContent = step.badge || '';
    overlayEl.querySelector('#arym-tour-callout-title').textContent = step.title;
    overlayEl.querySelector('#arym-tour-callout-desc').textContent = step.description;

    const prevBtn = overlayEl.querySelector('#arym-tour-prev');
    const nextBtn = overlayEl.querySelector('#arym-tour-next');
    prevBtn.style.visibility = stepIdx === 0 ? 'hidden' : 'visible';
    nextBtn.textContent = stepIdx === totalSteps - 1 ? 'Hoàn tất ✓' : 'Tiếp theo →';

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const calloutWidth = Math.min(360, vw - 28);
    const calloutHeight = calloutEl.offsetHeight || 195;

    let calloutLeft = bannerLeft + bannerWidth + 42;
    let calloutTop = bannerTop;
    let arrowStartX = bannerLeft + bannerWidth;
    let arrowStartY = bannerTop + bannerHeight / 2;
    let arrowEndX = calloutLeft - 6;
    let arrowEndY = calloutTop + 36;

    // When button is in the Bottom Floating Dock or lower third of screen, place callout frame ABOVE the button
    if (bannerTop > vh * 0.60) {
      calloutLeft = Math.min(Math.max(16, bannerLeft + bannerWidth / 2 - calloutWidth / 2), vw - calloutWidth - 16);
      calloutTop = Math.max(16, bannerTop - calloutHeight - 52);
      arrowStartX = bannerLeft + bannerWidth / 2;
      arrowStartY = bannerTop - 2;
      arrowEndX = Math.min(Math.max(calloutLeft + 44, arrowStartX + 18), calloutLeft + calloutWidth - 44);
      arrowEndY = calloutTop + calloutHeight + 6;
    } else if (calloutLeft + calloutWidth > vw - 16) {
      calloutLeft = Math.min(Math.max(14, bannerLeft), vw - calloutWidth - 14);
      if (bannerTop + bannerHeight + calloutHeight + 54 < vh) {
        calloutTop = bannerTop + bannerHeight + 44;
        arrowStartX = bannerLeft + bannerWidth / 2;
        arrowStartY = bannerTop + bannerHeight;
        arrowEndX = Math.min(Math.max(calloutLeft + 40, arrowStartX), calloutLeft + calloutWidth - 40);
        arrowEndY = calloutTop - 6;
      } else {
        calloutTop = Math.max(14, bannerTop - calloutHeight - 44);
        arrowStartX = bannerLeft + bannerWidth / 2;
        arrowStartY = bannerTop;
        arrowEndX = Math.min(Math.max(calloutLeft + 40, arrowStartX), calloutLeft + calloutWidth - 40);
        arrowEndY = calloutTop + calloutHeight + 6;
      }
    } else {
      calloutTop = Math.min(Math.max(16, bannerTop - 6), vh - calloutHeight - 16);
      arrowEndY = Math.min(Math.max(calloutTop + 28, arrowStartY), calloutTop + calloutHeight - 28);
    }

    calloutEl.style.left = `${ Math.round(calloutLeft) }px`;
    calloutEl.style.top = `${ Math.round(calloutTop) }px`;

    const ctrlX = Math.round((arrowStartX + arrowEndX) / 2);
    const ctrlY = Math.round(arrowStartY);
    arrowPathEl.setAttribute(
      'd',
      `M ${Math.round(arrowStartX)},${Math.round(arrowStartY)} Q ${ctrlX},${ctrlY} ${Math.round(arrowEndX)},${Math.round(arrowEndY)}`
    );
  }

  function renderCurrentStep() {
    const steps = findAvailableSteps();
    if (!steps.length) {
      finishTour(false);
      return;
    }
    if (currentStepIndex >= steps.length) {
      currentStepIndex = steps.length - 1;
    }
    const step = steps[currentStepIndex];
    const targetEl = document.querySelector(step.selector);
    if (!targetEl) {
      finishTour(false);
      return;
    }
    positionBannerAndCallout(targetEl, step, currentStepIndex, steps.length);
  }

  function startTour(options) {
    const opts = options || {};
    ensureOverlayElements();
    const steps = findAvailableSteps();
    if (!steps.length) {
      return false;
    }
    currentStepIndex = 0;
    activeCompletionCallback = typeof opts.onComplete === 'function' ? opts.onComplete : null;
    overlayEl.classList.add('is-active');
    renderCurrentStep();

    if (!resizeHandler) {
      resizeHandler = () => {
        if (overlayEl && overlayEl.classList.contains('is-active')) {
          renderCurrentStep();
        }
      };
      window.addEventListener('resize', resizeHandler);
    }
    return true;
  }

  function finishTour(invokeCallback) {
    if (overlayEl) {
      overlayEl.classList.remove('is-active');
    }
    if (resizeHandler) {
      window.removeEventListener('resize', resizeHandler);
      resizeHandler = null;
    }
    const cb = activeCompletionCallback;
    activeCompletionCallback = null;
    if (invokeCallback && cb) {
      cb();
    }
  }

  window.ARYMOnboardingTour = {
    start: startTour,
    stop: () => finishTour(false),
    steps: TOUR_STEPS,
  };
})();
