<script setup>
import {computed, onBeforeUnmount, ref, watch} from 'vue'

// 结果组件直接读取后端地址，避免额外改父组件传参。
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL

const props = defineProps({
  result: {
    type: Object,
    default: null
  },
  error: {
    type: String,
    default: ''
  },
  sourceFiles: {
    type: Array,
    default: () => []
  }
})

const PAGE_SIZE = 5
const IDEAL_MIN_SECONDS = 5
const IDEAL_MAX_SECONDS = 15

const previewVideo = ref(null)
const timelinePlot = ref(null)
const selectedVideoKey = ref('')
const currentPage = ref(0)
const currentPreviewTime = ref(0)
const previewDuration = ref(0)
const thresholdPercent = ref(35)
const isSubmittingSplit = ref(false)
const splitSubmitMessage = ref('')
const splitSubmitError = ref('')
const previewUrlCache = new Map()

// 保存手动点击后的“强制保留 / 强制删除”状态。
// 未覆盖时，按阈值线自动判断。
const manualMarkerStates = ref({})

const successfulResults = computed(() => {
  return props.result?.results?.filter((item) => item.success) ?? []
})

const totalPages = computed(() => {
  if (!successfulResults.value.length) {
    return 1
  }
  return Math.ceil(successfulResults.value.length / PAGE_SIZE)
})

const pagedVideoItems = computed(() => {
  const start = currentPage.value * PAGE_SIZE
  return successfulResults.value.slice(start, start + PAGE_SIZE)
})

const selectedResult = computed(() => {
  if (!successfulResults.value.length) {
    return null
  }

  return (
      successfulResults.value.find((item) => getResultKey(item) === selectedVideoKey.value) ??
      successfulResults.value[0]
  )
})

const selectedSourceFile = computed(() => {
  if (!selectedResult.value) {
    return null
  }

  return props.sourceFiles.find((file) => file.name === selectedResult.value.sourceName) ?? null
})

const selectedPreviewUrl = computed(() => {
  if (!selectedSourceFile.value) {
    return ''
  }

  return getPreviewUrl(selectedSourceFile.value)
})

// 标准化 scene 数据：
// 1. 过滤掉落在视频结束位置的切点
// 2. 强制保留 0s 起始切点
function normalizeScenes(scenes) {
  const rawScenes = Array.isArray(scenes) ? scenes : []
  if (!rawScenes.length) {
    return []
  }

  const clonedScenes = rawScenes
      .map((scene) => ({...scene}))
      .filter((scene) => typeof scene.startSeconds === 'number' && typeof scene.endSeconds === 'number')

  if (!clonedScenes.length) {
    return []
  }

  const maxEndSeconds = clonedScenes.reduce((maxValue, scene) => {
    return Math.max(maxValue, scene.endSeconds ?? 0)
  }, 0)

  const filteredScenes = clonedScenes.filter((scene) => scene.startSeconds < maxEndSeconds)
  if (!filteredScenes.length) {
    return []
  }

  const firstScene = filteredScenes[0]
  if (firstScene.startSeconds > 0) {
    filteredScenes.unshift({
      ...firstScene,
      index: 0,
      startSeconds: 0,
      durationSeconds: firstScene.endSeconds
    })
  } else {
    filteredScenes[0] = {
      ...firstScene,
      startSeconds: 0,
      durationSeconds: (firstScene.endSeconds ?? 0)
    }
  }

  return filteredScenes
}

function getSceneKey(scene) {
  return `${scene.index}-${scene.startSeconds}`
}

function getResultKey(item) {
  return item?.videoId || item?.sourceName || ''
}

function getFileKey(file) {
  return `${file.name}-${file.size}-${file.lastModified}`
}

function getPreviewUrl(file) {
  const fileKey = getFileKey(file)
  if (!previewUrlCache.has(fileKey)) {
    previewUrlCache.set(fileKey, URL.createObjectURL(file))
  }
  return previewUrlCache.get(fileKey)
}

function formatSeconds(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-'
  }

  return `${value.toFixed(3)}s`
}

function formatPercent(value) {
  return `${Math.min(Math.max(value, 0), 100)}%`
}

// 为任意一个视频结果构造编辑器状态。
// 这样“当前视频处理”和“全部视频一键处理”都能复用同一套计算。
function buildVideoState(resultItem) {
  const scenes = normalizeScenes(resultItem?.scenes)
  if (!scenes.length) {
    return {
      resultKey: getResultKey(resultItem),
      sourceName: resultItem?.sourceName ?? '',
      videoId: resultItem?.videoId ?? '',
      storedName: resultItem?.storedName ?? '',
      scenes: [],
      markers: [],
      videoDurationSeconds: 0,
      splitPoints: [],
      droppedPoints: [],
      whiteMarkerCount: 0,
      greenMarkerCount: 0,
      keptMarkerCount: 0,
      droppedMarkerCount: 0,
      hasWhiteMarker: false
    }
  }

  const resultKey = getResultKey(resultItem)
  const overrides = manualMarkerStates.value[resultKey] ?? {}
  const videoDurationSeconds = scenes[scenes.length - 1].endSeconds ?? 0
  const maxSegmentDuration = scenes.reduce((maxValue, scene) => {
    return Math.max(maxValue, scene.durationSeconds ?? 0)
  }, 0)

  const baseMarkers = scenes.map((scene) => {
    const sceneKey = getSceneKey(scene)
    const positionPercent = videoDurationSeconds ? (scene.startSeconds / videoDurationSeconds) * 100 : 0
    const heightPercent = maxSegmentDuration
        ? Math.max((scene.durationSeconds / maxSegmentDuration) * 100, 10)
        : 10
    const autoKeep = heightPercent >= thresholdPercent.value
    const override = overrides[sceneKey]
    const isKept = override ? override === 'keep' : autoKeep

    return {
      ...scene,
      sceneKey,
      positionPercent,
      heightPercent,
      autoKeep,
      isKept
    }
  })

  const markers = baseMarkers.map((marker, index) => {
    let mergedDurationSeconds = marker.durationSeconds ?? 0

    // 如果后面连续是红点，这些点不会真正切开视频，
    // 所以它们的时长需要先并入当前真正的分割点。
    if (marker.isKept) {
      let nextIndex = index + 1
      while (nextIndex < baseMarkers.length && !baseMarkers[nextIndex].isKept) {
        mergedDurationSeconds += baseMarkers[nextIndex].durationSeconds ?? 0
        nextIndex += 1
      }
    }

    const isDurationInRange =
        mergedDurationSeconds >= IDEAL_MIN_SECONDS && mergedDurationSeconds <= IDEAL_MAX_SECONDS
    const markerState = !marker.isKept ? 'drop' : isDurationInRange ? 'ideal' : 'split'

    return {
      ...marker,
      mergedDurationSeconds,
      isDurationInRange,
      markerState
    }
  })

  const splitPoints = markers.filter((marker) => marker.isKept).map((marker) => marker.startSeconds)
  const droppedPoints = markers.filter((marker) => !marker.isKept).map((marker) => marker.startSeconds)
  const whiteMarkerCount = markers.filter((marker) => marker.isKept && !marker.isDurationInRange).length
  const greenMarkerCount = markers.filter((marker) => marker.isKept && marker.isDurationInRange).length
  const keptMarkerCount = markers.filter((marker) => marker.isKept).length
  const droppedMarkerCount = markers.filter((marker) => !marker.isKept).length

  return {
    resultKey,
    sourceName: resultItem.sourceName,
    videoId: resultItem.videoId,
    storedName: resultItem.storedName,
    scenes,
    markers,
    videoDurationSeconds,
    splitPoints,
    droppedPoints,
    whiteMarkerCount,
    greenMarkerCount,
    keptMarkerCount,
    droppedMarkerCount,
    hasWhiteMarker: whiteMarkerCount > 0
  }
}

const selectedVideoState = computed(() => {
  if (!selectedResult.value) {
    return buildVideoState(null)
  }
  return buildVideoState(selectedResult.value)
})

const timelineMarkers = computed(() => selectedVideoState.value.markers)

const keptMarkerCount = computed(() => selectedVideoState.value.keptMarkerCount)
const droppedMarkerCount = computed(() => selectedVideoState.value.droppedMarkerCount)
const inRangeMarkerCount = computed(() => selectedVideoState.value.greenMarkerCount)
const whiteMarkerCount = computed(() => selectedVideoState.value.whiteMarkerCount)

const anyWhiteMarkers = computed(() => {
  return successfulResults.value.some((item) => buildVideoState(item).hasWhiteMarker)
})

const timelineDuration = computed(() => {
  if (previewDuration.value > 0) {
    return previewDuration.value
  }
  return selectedVideoState.value.videoDurationSeconds
})

const playheadPercent = computed(() => {
  if (!timelineDuration.value) {
    return 0
  }
  return (currentPreviewTime.value / timelineDuration.value) * 100
})

watch(
    successfulResults,
    (items) => {
      if (!items.length) {
        selectedVideoKey.value = ''
        currentPage.value = 0
        return
      }

      const hasSelected = items.some((item) => getResultKey(item) === selectedVideoKey.value)
      if (!hasSelected) {
        selectedVideoKey.value = getResultKey(items[0])
      }

      const selectedIndex = items.findIndex((item) => getResultKey(item) === selectedVideoKey.value)
      if (selectedIndex >= 0) {
        currentPage.value = Math.floor(selectedIndex / PAGE_SIZE)
      }
    },
    {immediate: true}
)

watch(selectedResult, () => {
  currentPreviewTime.value = 0
  previewDuration.value = 0
  splitSubmitMessage.value = ''
  splitSubmitError.value = ''
})

watch(
    () => props.sourceFiles,
    (files) => {
      const validFileKeys = new Set(files.map((file) => getFileKey(file)))
      Array.from(previewUrlCache.keys()).forEach((cacheKey) => {
        if (!validFileKeys.has(cacheKey)) {
          URL.revokeObjectURL(previewUrlCache.get(cacheKey))
          previewUrlCache.delete(cacheKey)
        }
      })
    },
    {immediate: true}
)

onBeforeUnmount(() => {
  stopThresholdDrag()
  previewUrlCache.forEach((previewUrl) => URL.revokeObjectURL(previewUrl))
  previewUrlCache.clear()
})

function onVideoLoadedMetadata(event) {
  previewDuration.value = event.target.duration || 0
}

function onVideoTimeUpdate(event) {
  currentPreviewTime.value = event.target.currentTime || 0
}

function goToPreviousPage() {
  currentPage.value = Math.max(currentPage.value - 1, 0)
}

function goToNextPage() {
  currentPage.value = Math.min(currentPage.value + 1, totalPages.value - 1)
}

function selectVideo(item) {
  selectedVideoKey.value = getResultKey(item)
}

// 单击竖线只切换“是否作为分割点”，颜色由后续状态重新计算。
function toggleMarker(marker) {
  const resultKey = getResultKey(selectedResult.value)
  if (!resultKey) {
    return
  }

  splitSubmitMessage.value = ''
  splitSubmitError.value = ''

  const nextStates = {...manualMarkerStates.value}
  const resultStates = {...(nextStates[resultKey] ?? {})}

  if (marker.isKept === marker.autoKeep) {
    resultStates[marker.sceneKey] = marker.autoKeep ? 'drop' : 'keep'
  } else {
    delete resultStates[marker.sceneKey]
  }

  nextStates[resultKey] = resultStates
  manualMarkerStates.value = nextStates
}

// 双击黑底区域时，只同步预览播放位置，不修改分割点。
function seekPreviewByDoubleClick(event) {
  if (!timelinePlot.value || !timelineDuration.value) {
    return
  }

  const rect = timelinePlot.value.getBoundingClientRect()
  const offsetX = event.clientX - rect.left
  const ratio = rect.width ? offsetX / rect.width : 0
  const targetSeconds = timelineDuration.value * Math.min(Math.max(ratio, 0), 1)

  currentPreviewTime.value = targetSeconds
  if (previewVideo.value) {
    previewVideo.value.currentTime = targetSeconds
  }
}

function updateThresholdFromEvent(event) {
  if (!timelinePlot.value) {
    return
  }

  const rect = timelinePlot.value.getBoundingClientRect()
  const offsetY = event.clientY - rect.top
  const ratioFromTop = rect.height ? offsetY / rect.height : 0
  thresholdPercent.value = Math.min(Math.max((1 - ratioFromTop) * 100, 0), 100)
}

function onThresholdMouseMove(event) {
  updateThresholdFromEvent(event)
}

function stopThresholdDrag() {
  window.removeEventListener('mousemove', onThresholdMouseMove)
  window.removeEventListener('mouseup', stopThresholdDrag)
}

function startThresholdDrag(event) {
  event.preventDefault()
  splitSubmitMessage.value = ''
  splitSubmitError.value = ''
  updateThresholdFromEvent(event)
  window.addEventListener('mousemove', onThresholdMouseMove)
  window.addEventListener('mouseup', stopThresholdDrag)
}

function createSplitPayload(videoStates) {
  return {
    jobId: props.result?.jobId ?? '',
    thresholdPercent: Number(thresholdPercent.value.toFixed(2)),
    videos: videoStates.map((state) => ({
      videoId: state.videoId,
      sourceName: state.sourceName,
      storedName: state.storedName,
      videoDurationSeconds: state.videoDurationSeconds,
      splitPoints: state.splitPoints,
      droppedPoints: state.droppedPoints,
      markers: state.markers.map((marker) => ({
        index: marker.index,
        startSeconds: marker.startSeconds,
        endSeconds: marker.endSeconds,
        durationSeconds: marker.durationSeconds,
        mergedDurationSeconds: marker.mergedDurationSeconds,
        state: marker.markerState
      }))
    }))
  }
}

async function submitSplitPayload(scope) {
  if (isSubmittingSplit.value || !props.result?.jobId) {
    return
  }

  const targetStates =
      scope === 'single'
          ? selectedResult.value
              ? [selectedVideoState.value]
              : []
          : successfulResults.value.map((item) => buildVideoState(item))

  if (!targetStates.length) {
    splitSubmitError.value = '没有可提交的视频结果。'
    splitSubmitMessage.value = ''
    return
  }

  const hasWhiteMarker = targetStates.some((state) => state.hasWhiteMarker)
  if (hasWhiteMarker) {
    splitSubmitError.value = '存在白色分割线，必须先调整到没有白线后才能提交。'
    splitSubmitMessage.value = ''
    return
  }

  isSubmittingSplit.value = true
  splitSubmitError.value = ''
  splitSubmitMessage.value = ''

  try {
    const response = await fetch(`${apiBaseUrl}/api/split`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        scope,
        ...createSplitPayload(targetStates)
      })
    })

    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.message || '提交分割点失败')
    }

    splitSubmitMessage.value = data.message || '分割点已成功提交到后端。'
  } catch (error) {
    splitSubmitError.value = error instanceof Error ? error.message : '提交分割点失败'
  } finally {
    isSubmittingSplit.value = false
  }
}
</script>

<template>
  <section class="queue-card result-card">
    <div class="section-head">
      <h2>处理结果</h2>
      <div class="result-head-actions">
        <button
            type="button"
            class="split-action-button"
            :disabled="isSubmittingSplit || !selectedResult || whiteMarkerCount > 0"
            @click="submitSplitPayload('single')"
        >
          {{ isSubmittingSplit ? '提交中...' : '开始处理' }}
        </button>
        <button
            type="button"
            class="split-action-button is-secondary"
            :disabled="isSubmittingSplit || !successfulResults.length || anyWhiteMarkers"
            @click="submitSplitPayload('all')"
        >
          {{ isSubmittingSplit ? '提交中...' : '一键处理' }}
        </button>
        <span v-if="result">{{ result.message }}</span>
        <span v-else>选择视频后在这里查看预览和分割点编辑结果</span>
      </div>
    </div>

    <p v-if="error" class="result-error">{{ error }}</p>
    <p v-if="splitSubmitError" class="result-error">{{ splitSubmitError }}</p>
    <p v-if="splitSubmitMessage" class="result-success">{{ splitSubmitMessage }}</p>

    <div class="results-shell">
      <div class="video-strip">
        <button
            type="button"
            class="page-button"
            :disabled="currentPage === 0"
            @click="goToPreviousPage"
        >
          上一页
        </button>

        <div class="video-list-panel">
          <button
              v-for="item in pagedVideoItems"
              :key="getResultKey(item)"
              type="button"
              :class="['video-item-button', { 'is-active': getResultKey(item) === getResultKey(selectedResult || {}) }]"
              @click="selectVideo(item)"
          >
            <span class="video-item-title">{{ item.sourceName }}</span>
            <span class="video-item-meta">{{ item.sceneCount }} 个片段</span>
          </button>

          <div v-if="!successfulResults.length" class="video-item-placeholder">
            <span>暂无视频结果</span>
          </div>
        </div>

        <button
            type="button"
            class="page-button"
            :disabled="currentPage >= totalPages - 1 || !successfulResults.length"
            @click="goToNextPage"
        >
          下一页
        </button>
      </div>

      <div class="preview-panel">
        <div class="preview-box">
          <video
              v-if="selectedPreviewUrl"
              ref="previewVideo"
              class="preview-player"
              :src="selectedPreviewUrl"
              controls
              preload="metadata"
              @loadedmetadata="onVideoLoadedMetadata"
              @timeupdate="onVideoTimeUpdate"
          />

          <div v-else class="preview-empty">
            <span v-if="selectedResult">没有匹配到可预览的本地视频文件</span>
            <span v-else>视频预览区域</span>
          </div>
        </div>

        <div class="timeline-box">
          <div class="timeline-summary">
            <span v-if="selectedResult">{{ selectedResult.sourceName }}</span>
            <span v-else>等待处理结果</span>
            <span>阈值 {{ thresholdPercent.toFixed(1) }}%</span>
            <span>绿线 {{ inRangeMarkerCount }}</span>
            <span>白线 {{ whiteMarkerCount }}</span>
            <span>分割点 {{ keptMarkerCount }}</span>
            <span>非分割点 {{ droppedMarkerCount }}</span>
          </div>

          <div class="timeline-description">
            <span>红线表示不是分割点；白线表示是分割点，但是真实切出来的视频时长不在 5~15 秒内；绿线表示是分割点且真实切出来的视频时长在 5~15 秒内。</span>
          </div>

          <div class="timeline-scroll">
            <div class="timeline-track">
              <div ref="timelinePlot" class="timeline-plot" @dblclick="seekPreviewByDoubleClick">
                <button
                    v-for="marker in timelineMarkers"
                    :key="`${getResultKey(selectedResult || {})}-${marker.sceneKey}`"
                    type="button"
                    :class="['timeline-marker', `is-${marker.markerState}`]"
                    :style="{
                    left: formatPercent(marker.positionPercent),
                    height: formatPercent(marker.heightPercent)
                  }"
                    :title="`片段 ${marker.index} ${formatSeconds(marker.startSeconds)} - ${formatSeconds(marker.endSeconds)} 原始时长 ${formatSeconds(marker.durationSeconds)} 合并后时长 ${formatSeconds(marker.mergedDurationSeconds)}`"
                    @click.stop="toggleMarker(marker)"
                ></button>

                <div class="threshold-line" :style="{ bottom: formatPercent(thresholdPercent) }"></div>

                <button
                    type="button"
                    class="threshold-handle"
                    :style="{ bottom: formatPercent(thresholdPercent) }"
                    @mousedown="startThresholdDrag"
                >
                  阈值
                </button>

                <div
                    v-if="timelineDuration"
                    class="timeline-playhead"
                    :style="{ left: formatPercent(playheadPercent) }"
                ></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.result-card {
  margin-top: 24px;
}

.result-head-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 10px 12px;
}

.split-action-button {
  border: 0;
  border-radius: 10px;
  padding: 8px 14px;
  background: #2563eb;
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}

.split-action-button.is-secondary {
  background: #0f766e;
}

.split-action-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.results-shell {
  display: grid;
  gap: 16px;
}

.video-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.video-list-panel {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.page-button {
  width: 72px;
  height: 72px;
  border: 1px solid rgba(22, 32, 42, 0.18);
  border-radius: 999px;
  background: #fff;
  cursor: pointer;
}

.page-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.video-item-button,
.video-item-placeholder {
  min-height: 72px;
  padding: 12px;
  border: 1px solid rgba(22, 32, 42, 0.18);
  border-radius: 8px;
  background: #fff;
  text-align: left;
}

.video-item-button {
  cursor: pointer;
}

.video-item-button.is-active {
  border-color: #2563eb;
}

.video-item-title,
.video-item-meta {
  display: block;
}

.video-item-title {
  font-weight: 700;
  overflow-wrap: anywhere;
}

.video-item-meta {
  margin-top: 6px;
  color: #647688;
  font-size: 0.9rem;
}

.preview-panel {
  display: grid;
  gap: 12px;
}

.preview-box,
.timeline-box {
  border: 1px solid rgba(22, 32, 42, 0.18);
  border-radius: 8px;
  background: #fff;
}

.preview-box {
  min-height: 360px;
  overflow: hidden;
}

.preview-player {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
}

.preview-empty {
  display: grid;
  place-items: center;
  min-height: 360px;
  padding: 20px;
  color: #647688;
}

.timeline-box {
  padding: 12px;
}

.timeline-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 8px;
  color: #44576a;
}

.timeline-description {
  margin-bottom: 12px;
  color: #647688;
}

.timeline-scroll {
  overflow-x: auto;
}

.timeline-track {
  min-width: 900px;
  padding: 10px 0;
  border-radius: 6px;
  background: #0a0a0a;
}

.timeline-plot {
  position: relative;
  height: 170px;
  margin: 0 10px;
}

.timeline-marker {
  position: absolute;
  bottom: 0;
  width: 1px;
  margin-left: -1px;
  padding: 0;
  border: 0;
  cursor: pointer;
  z-index: 2;
}

.timeline-marker.is-ideal {
  background: #22c55e;
}

.timeline-marker.is-split {
  background: #ffffff;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.25);
}

.timeline-marker.is-drop {
  background: #ef4444;
}

.threshold-line {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background: #22c55e;
  z-index: 1;
}

.threshold-handle {
  position: absolute;
  right: 0;
  transform: translateY(50%);
  padding: 2px 8px;
  border: 0;
  border-radius: 999px;
  background: #22c55e;
  color: #052e16;
  font-size: 0.8rem;
  cursor: ns-resize;
  z-index: 3;
}

.timeline-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: #facc15;
  z-index: 4;
}

.result-error {
  margin: 12px 0 0;
  color: #ad3d3d;
}

.result-success {
  margin: 12px 0 0;
  color: #217346;
}

@media (max-width: 900px) {
  .video-strip {
    grid-template-columns: 1fr;
  }

  .video-list-panel {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  }

  .page-button {
    width: 100%;
    height: 44px;
    border-radius: 8px;
  }

  .preview-box,
  .preview-empty {
    min-height: 240px;
  }

  .result-head-actions {
    justify-content: flex-start;
  }
}
</style>
