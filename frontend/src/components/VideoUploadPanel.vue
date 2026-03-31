<script setup>
import {computed, ref} from 'vue'

// 后端服务地址从vite的环境变量里读取，对应配置文件: frontend/.env
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL
// 上传队列本体，里面保存的是浏览器File对象
const files = ref([])
// 控制"开始处理"按钮的提交中状态，避免重复点击
const isSubmitting = ref(false)
// 控制"展示更多"弹窗是否打开
const isQueueModalOpen = ref(false)
// 拖拽文件进入上传框时，用来控制高亮态
const isDragOver = ref(false)
// 浏览器的dragenter/dragleave会触发多次，这里用一个计数器处理嵌套触发，避免鼠标还在区域内时高亮提前消失
const dragDepth = ref(0)
// 卡片里只预览前3个文件，避免主界面被长列表撑得太高
const visibleFiles = computed(() => files.value.slice(0, 3))
// 超过3个文件时，显示"展示更多"按钮并允许打开弹窗
const hasMoreFiles = computed(() => files.value.length > 3)

// 生成一个稳定的文件唯一标识，这里用文件名+大小+最后修改时间来区分，给当前上传场景使用
function getFileId(file) {
  return `${file.name}-${file.size}-${file.lastModified}`
}

// 只保留视频文件，大多数情况下浏览器会给出MIME类型；如果没有，就退回到扩展名判断
function normalizeFiles(fileList) {
  return Array.from(fileList || []).filter((file) => {
    if (file.type) {
      return file.type.startsWith('video/')
    }
    return /\.(mpe|mpeg|ogm|mkv|mpg|wmv|webm|ogv|mov|m4v|asx|mp4|avi)$/i.test(file.name)
  })
}

// 把新文件追加进现有上传队列，而不是覆盖旧队列，同时做一次去重，避免同一个文件被重复加入多次
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

// 点击input选择文件时走这里，选择完成后手动清空input.value，这样同一个文件下次还能再次触发change事件
function onFileChange(event) {
  appendFiles(event.target.files)
  event.target.value = ''
}

// 删除上传队列中的单个文件，如果删完以后文件数不超过3，就顺手把"展示更多"弹窗关掉
function removeFile(fileId) {
  files.value = files.value.filter((file) => getFileId(file) !== fileId)
  if (files.value.length <= 3) {
    isQueueModalOpen.value = false
  }
}

// 只有在文件数量超过3个时，才允许打开完整队列弹窗
function openQueueModal() {
  if (!hasMoreFiles.value) {
    return
  }
  isQueueModalOpen.value = true
}

function closeQueueModal() {
  isQueueModalOpen.value = false
}

// 文件拖进上传区域时开启高亮
function onDragEnter(event) {
  event.preventDefault()
  dragDepth.value += 1
  isDragOver.value = true
}

// 持续阻止浏览器默认行为，否则某些浏览器会尝试直接打开文件
function onDragOver(event) {
  event.preventDefault()
  isDragOver.value = true
}

// 文件拖离上传区域时，按层级递减计数，只有真正完全离开后，才取消高亮
function onDragLeave(event) {
  event.preventDefault()
  dragDepth.value = Math.max(0, dragDepth.value - 1)
  if (dragDepth.value === 0) {
    isDragOver.value = false
  }
}

// 把拖拽进来的文件追加到上传队列里
function onDrop(event) {
  event.preventDefault()
  dragDepth.value = 0
  isDragOver.value = false
  appendFiles(event.dataTransfer?.files)
}

// 点击"开始处理"后，把当前队列中的所有文件打包成FormData发给后端
async function submitVideos() {
  if (!files.value.length || isSubmitting.value) {
    return
  }
  isSubmitting.value = true
  const formData = new FormData()
  files.value.forEach((file) => {
    formData.append('videos', file)
  })
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
      <!-- 上传入口 -->
      <label
          class="upload-box"
          :class="{ 'is-drag-over': isDragOver }"
          @dragenter="onDragEnter"
          @dragover="onDragOver"
          @dragleave="onDragLeave"
          @drop="onDrop"
      >
        <span>上传视频文件</span>
        <small>点击此处选择视频文件进行上传/拖拽视频文件到此处进行上传</small>
        <input type="file" accept="video/*" multiple @change="onFileChange"/>
      </label>

      <!-- 当没有文件时禁用按钮，提交中显示处理中状态 -->
      <button class="submit-button" :disabled="!files.length || isSubmitting" @click="submitVideos">
        {{ isSubmitting ? '处理中...' : '开始处理' }}
      </button>

      <!-- 上传队列卡片，主界面只展示前3个文件 -->
      <section class="queue-card">
        <div class="section-head">
          <h2>上传队列</h2>
          <span>{{ files.length }} 个文件</span>
        </div>

        <!-- 队列预览，每项都可以单独删除 -->
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

        <!-- 文件数量超过3个时，用弹窗承载完整列表 -->
        <div v-if="hasMoreFiles" class="queue-footer">
          <button class="more-button" type="button" @click="openQueueModal">展示更多</button>
        </div>

        <!-- 队列为空时显示空状态文案 -->
        <p v-else-if="!files.length" class="empty-state">暂无视频文件</p>
      </section>
    </section>

    <!-- 完整上传队列弹窗，点击遮罩空白处可以关闭 -->
    <div v-if="isQueueModalOpen" class="modal-backdrop" @click.self="closeQueueModal">
      <section class="modal-card" role="dialog" aria-modal="true" aria-label="上传队列">
        <div class="modal-head">
          <div>
            <h3>上传队列</h3>
            <p>{{ files.length }} 个文件</p>
          </div>

          <button class="close-button" type="button" @click="closeQueueModal">关闭</button>
        </div>

        <!-- 弹窗里显示完整队列，删除行为与主卡片保持一致 -->
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
