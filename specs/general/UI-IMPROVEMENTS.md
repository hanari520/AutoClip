# UI/UX Improvements

## Summary

Reviewed the AutoClip Web Console, focusing on `web_console.py` and the light wrapper `web_console_light.py`.

The console already has a strong operational foundation: mobile-friendly tabs, live SSE logs, upload manifest states, light/dark theme support, file preview, batch upload selection, and artifact-backed status concepts. The main UX gaps are around information hierarchy, actionable empty states, error recovery, and reducing ambiguity between "投稿", "流转", and "日志" workflows.

## Critical Issues

### Issue: Destructive And High-Risk Actions Need Stronger Confirmation Context

**Current State**: `abortPipeline()` confirms "确定中断流水线？", while the pipeline page has a danger button labeled "重启服务" that actually posts to `/api/pipeline/abort` and reloads the page. Batch upload submission starts after selected items are posted with only a toast.

**Problem**: Users may confuse abort, reload, restart, and stop. Batch upload is also a high-risk action because it can publish externally to Bilibili.

**Recommendation**:

- Rename "重启服务" to "中断当前任务" if it only aborts the pipeline.
- Add a confirmation modal before batch upload with selected count, already-uploaded count, failed retry count, and cooldown rule.
- In destructive confirmations, show the current folder and current step.

**Impact**: Reduces accidental interruption and accidental external publishing.

**Implementation Notes**: Update `web_console.py` pipeline action text around the pipeline view and `submitUploads()` confirmation flow.

### Issue: Error Feedback Is Mostly Toast-Only

**Current State**: Many failure paths use short `toast()` messages, such as preview errors, startup failures, missing selections, and API errors.

**Problem**: Toasts disappear quickly and do not preserve enough context for recovery. On mobile, users can miss them while switching tabs.

**Recommendation**:

- Add an inline error region per major view: file browser, upload list, pipeline, logs.
- For failed requests, show action, reason, and next step. Example: "启动失败：未选择主播和文件夹。请到投稿页选择后再返回流转页。"
- Keep the latest error visible until dismissed or fixed.

**Impact**: Faster recovery and less repeated trial-and-error.

**Implementation Notes**: Add view-local `.alert` components and a small `showViewError(view, message, actionText?)` helper.

## High Priority Improvements

### Issue: Pipeline And Upload Information Hierarchy Compete

**Current State**: Upload page, pipeline page, and logs page all show overlapping status. The pipeline page depends on streamer/folder selection from the upload page.

**Problem**: Users may not understand why "一键流转" requires choices made elsewhere. The mental model is split across tabs.

**Recommendation**:

- On the pipeline page, surface the selected streamer/folder as a prominent "当前运行目录" block.
- If missing, replace the run button area with an empty state CTA: "先选择录播目录" with a button to jump to the upload/setup view.
- Add a compact step summary: selected steps, optional substeps, and expected output.

**Impact**: Makes the primary flow self-explanatory and reduces tab hopping.

**Implementation Notes**: `startPipeline()` already validates missing streamer/folder; move that guidance into visible pre-run UI, not only toast.

### Issue: Empty States Are Too Passive

**Current State**: Empty states include "加载中...", "空目录", "未找到投稿清单，请先运行流水线生成切片", and "暂无".

**Problem**: Most empty states describe the absence but do not provide a direct next action. Users must infer what to do.

**Recommendation**:

- File browser empty: show "上传文件" and "返回上级目录" actions when appropriate.
- Upload manifest empty: show "运行一键流转" and "重新扫描目录" actions.
- Quick dirs empty: explain where streamer folders are expected.
- Logs empty: show "启动一键流转后这里会显示实时日志".

**Impact**: Turns dead ends into guided workflow steps.

**Implementation Notes**: Replace `.empty` HTML fragments with reusable empty-state templates.

### Issue: Upload Cards Need Stronger Status Differentiation

**Current State**: Upload cards display status, attempt count, BV, and last error in one text line.

**Problem**: Completed, failed, skipped, and pending items are visually too similar when scanning a large manifest.

**Recommendation**:

- Use left status rail or badge color per item: success, failed, skipped, pending.
- For failed cards, expose a short "重试" or "选中重试" action.
- For success cards, show BVID as a copyable link-style element.
- Keep `last_error` on a second line with a warning/error icon and tooltip for full text.

**Impact**: Makes batch upload recovery much clearer.

**Implementation Notes**: Enhance `renderManifest()` card markup and add focused CSS classes.

### Issue: Logs Are Readable But Not Filterable

**Current State**: Light wrapper improves logs to timestamp + level + message and caps entries.

**Problem**: During long FFmpeg/ASR/upload runs, users still need to find failures manually.

**Recommendation**:

- Add level filters: All, Error, Warning, Success.
- Add "只看当前步骤" when pipeline step is known.
- Add a sticky latest-error strip above logs.

**Impact**: Faster diagnosis after long-running jobs.

**Implementation Notes**: Store log entries in a JS array before rendering instead of only appending DOM nodes.

## Medium Priority Enhancements

### Issue: Button Labels Mix Icons, Verbs, And Outcomes

**Current State**: Labels include "一键流转", "投稿", "流转", "清空", "重启服务", "全选", "反选", "深色".

**Problem**: Some labels are nouns, some are actions, and "重启服务" is misleading if it aborts a pipeline.

**Recommendation**:

- Prefer verb-object labels: "启动流转", "中断任务", "清空日志", "全选可投稿项".
- Keep tab labels short, but make primary buttons explicit.
- Avoid using emoji as the only differentiator.

**Impact**: Reduces ambiguity for repeated operational use.

### Issue: Theme Toggle Floats Independently From Navigation

**Current State**: Light wrapper injects a fixed-position theme button.

**Problem**: On narrow screens or device safe-area changes, it can feel detached from the rest of navigation.

**Recommendation**:

- Move theme toggle into header actions or settings tab.
- Keep it visible, but make its position part of the layout rather than fixed overlay.

**Impact**: Better visual hierarchy and less chance of overlap.

### Issue: Loading And Request States Are Inconsistent

**Current State**: Some async operations disable buttons, while others only toast. File preview, manifest loading, and status polling do not consistently show loading states.

**Recommendation**:

- Use a shared `setBusy(button, label)` helper.
- Show skeleton/inline spinner for manifest and file browser loading.
- Disable duplicate submit while request is in flight.

**Impact**: Improves perceived performance and prevents repeated clicks.

## Low Priority Suggestions

### Suggestion: Add Keyboard And Accessibility Basics

**Recommendation**:

- Add `aria-label` for icon-only controls.
- Ensure modal close buttons are keyboard reachable.
- Trap focus inside modal while open.
- Add Escape-to-close for preview and edit modals.

### Suggestion: Standardize Copy Tone

**Recommendation**:

- Use one tone across empty/error states: short title, one-line explanation, one primary action.
- Example pattern:
  - Title: "还没有投稿清单"
  - Body: "运行一键流转后会生成 upload_manifest.json。"
  - Action: "启动流转"

### Suggestion: Reduce Inline Styles Over Time

**Recommendation**:

- Move inline `style="..."` fragments in the embedded HTML to named CSS classes.
- This will make light/dark themes and responsive adjustments easier.

## Positive Observations

- The Light Web wrapper keeps backend routes intact and improves presentation without rewriting server behavior.
- Logs now have level semantics in the light wrapper, which is a strong foundation for filtering.
- Upload selection has useful safeguards: successful items are not selected by default.
- The interface is already mobile-aware with bottom tabs and desktop sidebar behavior at wider widths.
- The upload manifest model contains the right UX data: status, attempts, BVID, timestamps, and `last_error`.

