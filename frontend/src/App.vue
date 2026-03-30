<script setup>
import { computed, ref } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5000').replace(/\/$/, '')

const files = ref([])
const isSubmitting = ref(false)
const isQueueModalOpen = ref(false)
const isDragOver = ref(false)
const dragDepth = ref(0)

const defaultOptions = {
  threshold: 27,
  minSceneLen: 15,
  splitVideos: true
}

const visibleFiles = computed(() => files.value.slice(0, 3))
const hasMoreFiles = computed(() => files.value.length > 3)

function getFileId(file) {
  return `${file.name}-${file.size}-${file.lastModified}`
}

function normalizeFiles(fileList) {
  return Array.from(fileList || []).filter((file) => {
    if (file.type) {
      return file.type.startsWith('video/')
    }
    return /\.(mp4|mov|mkv|avi|webm|m4v|mpeg|mpg)$/i.test(file.name)
  })
}

function appendFiles(fileList) {
  const nextFiles = normalizeFiles(fileList)
  if (!nextFiles.length) {
    return
  }

  const mergedFiles = new Map(files.value.map((file) => [getFileId(file), file]))
  nextFiles.forEach((file) => {
    mergedFiles.set(getFileId(file), file)
  })
  files.value = Array.from(mergedFiles.values())
}

function onFileChange(event) {
  appendFiles(event.target.files)
  event.target.value = ''
}

function removeFile(fileId) {
  files.value = files.value.filter((file) => getFileId(file) !== fileId)
  if (files.value.length <= 3) {
    isQueueModalOpen.value = false
  }
}

function openQueueModal() {
  if (!hasMoreFiles.value) {
    return
  }
  isQueueModalOpen.value = true
}

function closeQueueModal() {
  isQueueModalOpen.value = false
}

function onDragEnter(event) {
  event.preventDefault()
  dragDepth.value += 1
  isDragOver.value = true
}

function onDragOver(event) {
  event.preventDefault()
  isDragOver.value = true
}

function onDragLeave(event) {
  event.preventDefault()
  dragDepth.value = Math.max(0, dragDepth.value - 1)
  if (dragDepth.value === 0) {
    isDragOver.value = false
  }
}

function onDrop(event) {
  event.preventDefault()
  dragDepth.value = 0
  isDragOver.value = false
  appendFiles(event.dataTransfer?.files)
}

async function submitVideos() {
  if (!files.value.length || isSubmitting.value) {
    return
  }

  isSubmitting.value = true

  const formData = new FormData()
  files.value.forEach((file) => {
    formData.append('videos', file)
  })
  formData.append('threshold', String(defaultOptions.threshold))
  formData.append('min_scene_len', String(defaultOptions.minSceneLen))
  formData.append('split_videos', String(defaultOptions.splitVideos))

  try {
    await fetch(`${apiBaseUrl}/api/process`, {
      method: 'POST',
      body: formData
    })
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <main class="page-shell">
    <section class="upload-panel">
      <label
        class="upload-box"
        :class="{ 'is-drag-over': isDragOver }"
        @dragenter="onDragEnter"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <span>选择视频文件</span>
        <small>点击选择，或直接把视频拖到这里</small>
        <input type="file" accept="video/*" multiple @change="onFileChange" />
      </label>

      <button class="submit-button" :disabled="!files.length || isSubmitting" @click="submitVideos">
        {{ isSubmitting ? '处理中...' : '开始处理' }}
      </button>

      <section class="queue-card">
        <div class="section-head">
          <h2>上传队列</h2>
          <span>{{ files.length }} 个文件</span>
        </div>

        <ul v-if="files.length" class="file-list">
          <li v-for="file in visibleFiles" :key="getFileId(file)">
            <div class="file-meta">
              <strong class="file-name">{{ file.name }}</strong>
              <span>{{ (file.size / 1024 / 1024).toFixed(2) }} MB</span>
            </div>
            <button class="remove-button" type="button" @click="removeFile(getFileId(file))">
              删除
            </button>
          </li>
        </ul>

        <div v-if="hasMoreFiles" class="queue-footer">
          <button class="more-button" type="button" @click="openQueueModal">展示更多</button>
        </div>

        <p v-else-if="!files.length" class="empty-state">当前还没有选择文件。</p>
      </section>
    </section>

    <div v-if="isQueueModalOpen" class="modal-backdrop" @click.self="closeQueueModal">
      <section class="modal-card" role="dialog" aria-modal="true" aria-label="上传队列">
        <div class="modal-head">
          <div>
            <h3>上传队列</h3>
            <p>{{ files.length }} 个文件</p>
          </div>
          <button class="close-button" type="button" @click="closeQueueModal">关闭</button>
        </div>

        <ul class="file-list modal-file-list">
          <li v-for="file in files" :key="`modal-${getFileId(file)}`">
            <div class="file-meta">
              <strong class="file-name">{{ file.name }}</strong>
              <span>{{ (file.size / 1024 / 1024).toFixed(2) }} MB</span>
            </div>
            <button class="remove-button" type="button" @click="removeFile(getFileId(file))">
              删除
            </button>
          </li>
        </ul>
      </section>
    </div>
  </main>
</template>
