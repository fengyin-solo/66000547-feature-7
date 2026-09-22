<template>
  <div class="panel">
    <div class="rule-head">
      <h4>🧩 判定规则 <span class="hint">（多选后可批量操作，切换日志类型不丢失）</span></h4>
      <div class="batch-bar">
        <el-button size="small" type="success" :disabled="!selected.length || batchLoading"
                   @click="submit('enable')">批量启用</el-button>
        <el-button size="small" type="info" :disabled="!selected.length || batchLoading"
                   @click="submit('disable')">批量停用</el-button>
        <el-button size="small" type="warning" :disabled="!selected.length || batchLoading"
                   @click="openKeywordDialog">改关键词命中词表</el-button>
        <span v-if="selected.length" class="sel-count">已选 {{ selected.length }} 条</span>
      </div>
    </div>
    <el-table :data="store.rules" size="small" max-height="260" stripe row-key="id"
              @selection-change="onSelectionChange" ref="tableRef">
      <el-table-column type="selection" width="38" reserve-selection/>
      <el-table-column prop="id" label="ID" width="48"/>
      <el-table-column prop="name" label="规则名称" width="110"/>
      <el-table-column label="类型" width="70">
        <template #default="{row}">
          <el-tag size="small" effect="plain">{{ typeLabel(row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="62">
        <template #default="{row}">
          <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="阈值/词表" show-overflow-tooltip>
        <template #default="{row}">
          <span v-if="row.type==='keyword'">
            {{ row.keywords && row.keywords.length ? row.keywords.join('、') : '（未设置词表）' }}
          </span>
          <span v-else>阈值 {{ row.threshold }}</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- 关键词词表编辑弹窗 -->
    <el-dialog v-model="kwDialogVisible" title="修改关键词命中词表" width="420px" append-to-body>
      <p class="dlg-tip">将把选中的 <b>{{ selected.length }}</b> 条规则的词表统一替换为以下关键词（逗号、空格或换行分隔）：</p>
      <el-input v-model="keywordText" type="textarea" :rows="4"
                placeholder="例如：timeout, oom, failed"/>
      <template #footer>
        <el-button @click="kwDialogVisible=false">取消</el-button>
        <el-button type="primary" :disabled="!parsedKeywords.length"
                   :loading="batchLoading" @click="confirmKeywords">提交批量修改</el-button>
      </template>
    </el-dialog>

    <!-- 逐条执行结果弹窗 -->
    <el-dialog v-model="resultVisible" title="批量操作执行结果" width="560px" append-to-body
               :close-on-click-modal="false">
      <el-alert v-if="lastResp?.idempotent" type="info" :closable="false" show-icon
                title="该批次此前已提交过，未重复生效，以下为首次执行的结果记录。" style="margin-bottom:10px"/>
      <div class="result-summary">
        共 {{ lastResp?.results.length || 0 }} 条：
        <span class="ok">成功 {{ lastResp?.successCount || 0 }}</span> /
        <span :class="{ fail: (lastResp?.failCount || 0) > 0 }">
          失败 {{ lastResp?.failCount || 0 }}
        </span>
      </div>
      <el-table :data="lastResp?.results || []" size="small" max-height="300" stripe
                :row-class-name="resultRowClass">
        <el-table-column label="规则" width="150">
          <template #default="{row}">
            #{{ row.ruleId }}<span v-if="row.ruleName"> {{ row.ruleName }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="76">
          <template #default="{row}">
            <el-tag size="small" :type="row.success ? 'success' : 'danger'">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" prop="message"/>
      </el-table>
      <template #footer>
        <el-button type="warning" @click="cancelAndResubmit">取消（重新下发）</el-button>
        <el-button type="primary" @click="resultVisible=false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useLogStore } from '../store/log'
import type { AlertRule, BatchOperation, BatchRuleResult, BatchRulesResponse } from '../types'

const store = useLogStore()
const tableRef = ref()
const selected = ref<AlertRule[]>([])
const batchLoading = ref(false)

const kwDialogVisible = ref(false)
const keywordText = ref('')
const parsedKeywords = computed(() =>
  Array.from(new Set(keywordText.value.split(/[,，\s\n]+/).map(s => s.trim().toLowerCase()).filter(Boolean)))
)

const resultVisible = ref(false)
const lastResp = ref<BatchRulesResponse | null>(null)

// 已下发且未取消的批次：同样的一批（操作+规则+词表）只允许生效一次
let lastBatch: { signature: string; batchId: string } | null = null

function onSelectionChange(rows: AlertRule[]) {
  selected.value = rows
}

function typeLabel(t: string) {
  return ({ level: '级别', count: '流量', keyword: '关键词' } as Record<string, string>)[t] || t
}

function openKeywordDialog() {
  if (!selected.value.length) return
  // 预填：取选中规则里第一条带词表的关键词规则
  const prefill = selected.value
    .filter(r => r.type === 'keyword')
    .flatMap(r => r.keywords || [])
  keywordText.value = Array.from(new Set(prefill)).join(', ')
  kwDialogVisible.value = true
}

function confirmKeywords() {
  if (!parsedKeywords.value.length) {
    ElMessage.warning('词表不能为空')
    return
  }
  kwDialogVisible.value = false
  runBatch('set_keywords', parsedKeywords.value)
}

function submit(operation: BatchOperation) {
  if (!selected.value.length) return
  runBatch(operation, [])
}

async function runBatch(operation: BatchOperation, keywords: string[]) {
  const ids = selected.value.map(r => r.id)
  const signature = [operation, ids.join('-'), keywords.join('|')].join('::')
  const batchId = lastBatch && lastBatch.signature === signature
    ? lastBatch.batchId
    : (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`)
  lastBatch = { signature, batchId }

  batchLoading.value = true
  try {
    const resp = await store.batchUpdateRules(operation, ids, keywords, batchId)
    lastResp.value = resp
    resultVisible.value = true
    if (resp.failCount > 0) {
      const failed = resp.results.filter(r => !r.success).map(r => `#${r.ruleId}`).join('、')
      ElMessage.error(`${resp.failCount} 条规则执行失败：${failed}`)
    } else if (!resp.idempotent) {
      ElMessage.success(`批量${opLabel(operation)}完成，共 ${resp.successCount} 条`)
    }
  } catch (e: any) {
    // 整批性错误（空词表、空选择、未知操作）：不算已生效批次，允许修正后重发
    lastBatch = null
    ElMessage.error(e?.response?.data?.detail || '批量操作提交失败')
  } finally {
    batchLoading.value = false
  }
}

async function cancelAndResubmit() {
  if (lastBatch) {
    await store.releaseBatch(lastBatch.batchId)
    lastBatch = null
  }
  resultVisible.value = false
  // 选择保留，用户可直接对同一组规则重新下发
  ElMessage.info('已取消该批次，可重新提交下发')
}

function opLabel(op: string) {
  return ({ enable: '启用', disable: '停用', set_keywords: '改词表' } as Record<string, string>)[op] || op
}

function resultRowClass({ row }: { row: BatchRuleResult }) {
  return row.success ? 'row-ok' : 'row-fail'
}
</script>

<style scoped>
.panel{background:#1e293b;border-radius:8px;padding:12px;border:1px solid #334155}
.rule-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px}
.panel h4{color:#38bdf8;font-size:13px}
.hint{color:#64748b;font-size:11px;font-weight:400}
.batch-bar{display:flex;gap:6px;align-items:center}
.sel-count{color:#94a3b8;font-size:11px}
.dlg-tip{font-size:12px;color:#94a3b8;margin-bottom:8px}
.result-summary{font-size:13px;margin-bottom:8px;color:#cbd5e1}
.result-summary .ok{color:#4ade80}
.result-summary .fail{color:#f87171;font-weight:700}
:deep(.row-fail){background:#7f1d1d33 !important}
:deep(.row-ok){}
</style>
