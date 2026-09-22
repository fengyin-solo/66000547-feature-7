<template>
  <div class="panel rule-panel">
    <div class="rule-head">
      <h4>🧩 判定规则</h4>
      <span class="hint">切换日志类型不会清空；已选 {{ selected.length }} 条</span>
    </div>

    <el-table :data="store.rules" size="small" max-height="240" stripe
              ref="tableRef" @selection-change="onSelectionChange" row-key="id">
      <el-table-column type="selection" width="36" reserve-selection/>
      <el-table-column prop="name" label="名称" width="110">
        <template #default="{row}">
          <span :class="{ disabled: !row.enabled }">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="type" label="类型" width="78">
        <template #default="{row}">
          <el-tag size="small" :type="row.type==='keyword'?'success':row.type==='level'?'danger':'warning'">{{ row.type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="阈值/词表" show-overflow-tooltip>
        <template #default="{row}">
          <span v-if="row.type==='keyword'">{{ row.keywords.join('、') || '（空词表）' }}</span>
          <span v-else>阈值 {{ row.threshold }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="64">
        <template #default="{row}">
          <el-tag size="small" :type="row.enabled?'success':'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <div class="batch-bar">
      <el-button size="small" type="success" :disabled="!selected.length" :loading="submitting"
                 @click="openBatch('enable')">▶ 批量启用</el-button>
      <el-button size="small" type="info" :disabled="!selected.length" :loading="submitting"
                 @click="openBatch('disable')">⏸ 批量停用</el-button>
      <el-button size="small" type="warning" :disabled="!selected.length"
                 @click="openBatch('set_keywords')">✎ 改关键词词表</el-button>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="560px"
               :close-on-click-modal="false" @closed="onDialogClosed">
      <!-- 阶段一：确认下发内容 -->
      <div v-if="phase==='edit'">
        <p class="dlg-line">
          将对以下 <b>{{ selected.length }}</b> 条规则执行「{{ actionLabel }}」：
        </p>
        <ul class="rule-preview">
          <li v-for="r in selected" :key="r.id">
            <el-tag size="small" :type="r.enabled?'success':'info'" class="mr6">{{ r.enabled?'启用':'停用' }}</el-tag>
            #{{ r.id }} {{ r.name }}
            <el-tag v-if="action==='set_keywords' && r.type!=='keyword'" size="small" type="danger" class="ml6">
              非关键词规则，将失败
            </el-tag>
          </li>
        </ul>
        <template v-if="action==='set_keywords'">
          <div class="dlg-line">命中关键词（逗号、空格或换行分隔）：</div>
          <el-input v-model="keywordText" type="textarea" :rows="3"
                    placeholder="例如：timeout, failed, oom"/>
          <div v-if="nonKeywordCount" class="warn-text">
            注意：所选规则中有 {{ nonKeywordCount }} 条不是关键词规则，服务端会逐条返回失败原因。
          </div>
        </template>
        <p class="batch-id">批次号：{{ batchId }}</p>
      </div>

      <!-- 阶段二：逐条执行结果 -->
      <div v-else>
        <el-alert v-if="lastResponse?.idempotent" type="warning" :closable="false" show-icon
                  title="这是该批次的重复提交，服务端回放了首次结果，规则没有再次改动。" class="mb8"/>
        <el-alert :type="lastResponse && lastResponse.failureCount===0 ? 'success' : 'error'"
                  :closable="false" show-icon class="mb8"
                  :title="summaryText"/>
        <ul class="result-list">
          <li v-for="item in lastResponse?.results" :key="item.ruleId" :class="item.success?'ok':'fail'">
            <span class="mark">{{ item.success ? '✅' : '❌' }}</span>
            <span class="rname">#{{ item.ruleId }} {{ item.ruleName || '(未知规则)' }}</span>
            <span class="rreason">
              <template v-if="item.success">
                {{ item.changed ? '已生效' : ('未改动：' + item.reason) }}
              </template>
              <template v-else>失败：{{ item.reason }}</template>
            </span>
          </li>
        </ul>
        <p v-if="lastResponse?.duplicatedIds?.length" class="warn-text">
          同一批次中重复勾选的规则 {{ lastResponse.duplicatedIds.join(', ') }} 只处理了一次。
        </p>
        <p v-if="redetected" class="ok-text">已按新规则重新检测，告警口径已同步。</p>
      </div>

      <template #footer>
        <template v-if="phase==='edit'">
          <el-button @click="cancelEdit">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="submit">提交</el-button>
        </template>
        <template v-else>
          <el-button @click="startNewBatch">用新批次重新下发</el-button>
          <el-button type="primary" @click="dialogVisible=false">完成</el-button>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useLogStore, newBatchId } from '../store/log'
import type { AlertRule, BatchAction, BatchResponse } from '../types'

const store = useLogStore()
const tableRef = ref()
const selected = ref<AlertRule[]>([])

const dialogVisible = ref(false)
const phase = ref<'edit' | 'result'>('edit')
const action = ref<BatchAction>('enable')
const batchId = ref('')
const keywordText = ref('')
const submitting = ref(false)
const lastResponse = ref<BatchResponse | null>(null)
const redetected = ref(false)

const ACTION_LABELS: Record<BatchAction, string> = {
  enable: '批量启用', disable: '批量停用', set_keywords: '改关键词词表'
}
const actionLabel = computed(() => ACTION_LABELS[action.value])
const dialogTitle = computed(() => `${actionLabel.value}（${phase.value === 'edit' ? '确认' : '逐条结果'}）`)
const nonKeywordCount = computed(() =>
  action.value === 'set_keywords' ? selected.value.filter(r => r.type !== 'keyword').length : 0
)
const summaryText = computed(() => {
  const r = lastResponse.value
  if (!r) return ''
  return `共 ${r.results.length} 条：成功 ${r.successCount} 条，失败 ${r.failureCount} 条。` +
    (r.failureCount > 0 ? '失败的规则及原因见下，其余规则已正常生效。' : '')
})

function onSelectionChange(rows: AlertRule[]) { selected.value = rows }

function openBatch(a: BatchAction) {
  if (!selected.value.length) { ElMessage.warning('请先勾选规则'); return }
  action.value = a
  // 打开对话框即生成批次号：同一批次重复提交只生效一次；取消后释放，可重新下发
  batchId.value = newBatchId()
  keywordText.value = selected.value.length === 1 && selected.value[0].type === 'keyword'
    ? selected.value[0].keywords.join(', ')
    : ''
  phase.value = 'edit'
  lastResponse.value = null
  redetected.value = false
  dialogVisible.value = true
}

function cancelEdit() {
  // 取消即释放该批次令牌；之后可以重新下发
  store.cancelBatch(batchId.value)
  dialogVisible.value = false
}

function onDialogClosed() {
  phase.value = 'edit'
  lastResponse.value = null
  redetected.value = false
}

async function submit() {
  const ids = selected.value.map(r => r.id)
  const keywords = [...new Set(
    keywordText.value.split(/[,，\s\n]+/).map(k => k.trim().toLowerCase()).filter(Boolean)
  )]
  if (action.value === 'set_keywords' && !keywords.length) {
    ElMessage.warning('请填写至少一个关键词')
    return
  }
  submitting.value = true
  try {
    lastResponse.value = await store.submitBatch(action.value, ids, keywords, batchId.value)
    phase.value = 'result'
    // 规则口径已变：若已有检测结果，立即按新规则重新检测，保证清单与结果一致
    if (store.result) {
      await store.detect()
      redetected.value = true
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || '批量操作失败'
    ElMessage.error(detail)
  } finally {
    submitting.value = false
  }
}

function startNewBatch() {
  // 放弃旧批次，换发新批次号；原令牌保留在服务端但不再影响新批次
  batchId.value = newBatchId()
  phase.value = 'edit'
  lastResponse.value = null
  redetected.value = false
}
</script>

<style scoped>
.rule-panel{background:#1e293b;border-radius:8px;padding:12px;border:1px solid #334155;margin-top:12px}
.rule-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.rule-head h4{color:#a78bfa;font-size:13px}
.hint{color:#64748b;font-size:11px}
.disabled{color:#64748b}
.batch-bar{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.dlg-line{font-size:13px;color:#334155;margin-bottom:8px}
.rule-preview{font-size:12px;color:#334155;list-style:none;max-height:160px;overflow:auto;margin-bottom:8px}
.rule-preview li{padding:3px 0}
.mr6{margin-right:6px}.ml6{margin-left:6px}
.warn-text{color:#b45309;font-size:12px;margin-top:8px}
.ok-text{color:#15803d;font-size:12px;margin-top:8px}
.batch-id{color:#94a3b8;font-size:11px;margin-top:10px;font-family:monospace}
.mb8{margin-bottom:8px}
.result-list{list-style:none;max-height:240px;overflow:auto;font-size:12px}
.result-list li{padding:5px 6px;border-radius:4px;margin-bottom:3px;display:flex;gap:8px;align-items:flex-start}
.result-list li.ok{background:#f0fdf4;border:1px solid #bbf7d0}
.result-list li.fail{background:#fef2f2;border:1px solid #fecaca}
.mark{flex:none}
.rname{font-weight:600;color:#1e293b;flex:none;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rreason{color:#475569}
</style>
