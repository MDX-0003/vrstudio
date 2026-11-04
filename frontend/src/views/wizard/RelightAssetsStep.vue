<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, Upload } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { fetchCase, pickRelightSlot } from '@/api/case'
import AssetPickerDialog from '@/components/assets/AssetPickerDialog.vue'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import FileRow from '@/components/common/FileRow.vue'
import { gateBlocked } from '@/utils/gates'
import type { CaseInfo, RelightSlot } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()
const router = useRouter()

const payload = computed(() => props.info.relight ?? null)
const gt = computed<RelightSlot | null>(() => payload.value?.gt ?? null)
const atmo = computed<RelightSlot | null>(() => payload.value?.atmo ?? null)
const media = computed(() => payload.value?.media ?? null)
const gate = computed(() => props.info.gates.assets)

const uploadOpen = ref(false)
const uploadRole = ref<'relight_gt' | 'relight_atmo'>('relight_gt')
const pickerOpen = ref(false)
const pickerRole = ref<'relight_gt' | 'relight_atmo'>('relight_gt')
const busy = ref(false)

/** 底版候选收视频与图片；氛围在底版是视频（或还没选）时收两种，图片底版只收图片。 */
const atmoAcceptsVideo = computed(() => gt.value?.kind !== 'image')
const pickerMode = computed<'video' | 'image'>(() => {
  if (pickerRole.value === 'relight_gt') return 'video'
  return atmoAcceptsVideo.value ? 'video' : 'image'
})
const gtCandidates = computed(
  () => props.info.assets.filter((asset) => asset.role === 'relight_gt').length,
)
const atmoCandidates = computed(
  () => props.info.assets.filter((asset) => asset.role === 'relight_atmo').length,
)

function openUpload(role: 'relight_gt' | 'relight_atmo'): void {
  uploadRole.value = role
  uploadOpen.value = true
}

function openPicker(role: 'relight_gt' | 'relight_atmo'): void {
  pickerRole.value = role
  pickerOpen.value = true
}

async function onPicked(ids: string[]): Promise<void> {
  const row = props.info.assets.find((asset) => asset.id === ids[0])
  pickerOpen.value = false
  if (!row) return
  if (busy.value) return
  busy.value = true
  try {
    const data = await pickRelightSlot(props.caseName, pickerRole.value, row.id)
    emit('updated', data)
    ElMessage.success(pickerRole.value === 'relight_gt' ? '底版已选定' : '氛围依据已选定')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换槽位失败')
  } finally {
    busy.value = false
  }
}

async function onUploaded(data: CaseInfo): Promise<void> {
  uploadOpen.value = false
  emit('updated', data)
  // 上传接口已经写入槽位；再拉一次拿到最新的门控与派生条目。
  emit('updated', await fetchCase(props.caseName))
}

function goRelight(): void {
  void router.push({ name: 'case', params: { name: props.caseName, step: 'relight' } })
}

function sizeText(slot: RelightSlot | null): string {
  if (!slot) return ''
  const parts = [slot.kind === 'video' ? '视频' : '图片']
  if (slot.entry.exists === false) parts.push('文件不存在')
  return parts.join(' · ')
}
</script>

<template>
  <div class="relight-assets">
    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">这一步决定重打光的两份输入</h2>
      </div>
      <div class="panel-body">
        <el-alert
          v-if="gate && !gate.ok"
          type="info"
          :closable="false"
          :title="gate.reason || '底版与氛围依据都选定后才能重打光'"
          class="gate-alert"
        />
        <el-alert
          v-else
          type="success"
          :closable="false"
          :title="gate?.evidence || '两个槽位都已选定'"
          class="gate-alert"
        />

        <div class="slot-grid">
          <article class="slot" :class="{ filled: Boolean(gt) }">
            <header>
              <h3>重打光底版</h3>
              <el-tag v-if="gt" size="small" effect="light">{{ sizeText(gt) }}</el-tag>
              <el-tag v-else size="small" effect="plain" type="info">未选</el-tag>
            </header>
            <p class="muted slot-help">
              提供结构与细节的真 GT：2:1 ERP 的视频或图片。视频走整段，图片只出一帧。
            </p>
            <FileRow v-if="gt" :entry="gt.entry" />
            <div class="slot-actions">
              <el-button size="small" :icon="FolderOpened" @click="openPicker('relight_gt')">
                从素材库选{{ gtCandidates ? `（${gtCandidates}）` : '' }}
              </el-button>
              <el-button size="small" :icon="Upload" @click="openUpload('relight_gt')">
                上传
              </el-button>
            </div>
          </article>

          <article class="slot" :class="{ filled: Boolean(atmo) }">
            <header>
              <h3>氛围依据</h3>
              <el-tag v-if="atmo" size="small" effect="light">{{ sizeText(atmo) }}</el-tag>
              <el-tag v-else size="small" effect="plain" type="info">未选</el-tag>
            </header>
            <p class="muted slot-help">
              只提供低频光照、色温与遮挡：与底版同宽高的图片，或视频底版下同时长的视频。
              图片走固定光场，视频走逐帧光场；fullredraw 的重绘成片可以直接传进来。
            </p>
            <FileRow v-if="atmo" :entry="atmo.entry" />
            <div class="slot-actions">
              <el-button size="small" :icon="FolderOpened" @click="openPicker('relight_atmo')">
                从素材库选{{ atmoCandidates ? `（${atmoCandidates}）` : '' }}
              </el-button>
              <el-button size="small" :icon="Upload" @click="openUpload('relight_atmo')">
                上传
              </el-button>
            </div>
          </article>
        </div>

        <div class="next-line">
          <el-button type="primary" :disabled="gateBlocked(gate)" @click="goRelight">
            去重打光
          </el-button>
          <span v-if="media" class="muted">
            底版是{{
              media === 'video'
                ? '视频：整段逐帧复用同一份光照场，只适用于固定机位'
                : '图片：只出一帧'
            }}
          </span>
        </div>
      </div>
    </section>

    <AssetPickerDialog
      v-model="pickerOpen"
      :case-name="caseName"
      :info="info"
      :mode="pickerMode"
      :title="pickerRole === 'relight_gt' ? '选择重打光底版' : '选择氛围依据'"
      :hint="
        pickerRole === 'relight_gt'
          ? '从本 case 的素材库里选一条底版候选。底版提供结构与细节，视频走整段、图片只出一帧。'
          : '从本 case 的素材库里选氛围依据：图片，或与视频底版同时长、只改氛围的重绘视频。'
      "
      :confirm-text="pickerRole === 'relight_gt' ? '选作底版' : '选作氛围依据'"
      @confirm="onPicked"
      @updated="emit('updated', $event)"
    />
    <AssetUploadDialog
      v-model="uploadOpen"
      destination="slot"
      :case-name="caseName"
      :slot-role="uploadRole"
      :slot-accepts-video="uploadRole === 'relight_gt' || atmoAcceptsVideo"
      @added="onUploaded"
    />
  </div>
</template>

<style scoped>
.relight-assets {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.slot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--space-4);
}

.slot {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-lg);
  background: var(--color-surface-muted);
}

.slot.filled {
  border-style: solid;
  background: var(--color-surface);
}

.slot header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.slot h3 {
  margin: 0;
  font-size: var(--text-md);
}

.slot-help {
  margin: 0;
  font-size: var(--text-xs);
}

.slot-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.gate-alert {
  margin-bottom: var(--space-3);
}

.next-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-xs);
}
</style>
