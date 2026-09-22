import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import axios from 'axios'
import type { AnalysisResult, AlertRule, BatchOperation, BatchRulesResponse } from '@/types'

const RULES_KEY = 'lad:rules'
const LOG_TYPE_KEY = 'lad:logType'

const DEFAULT_RULES: AlertRule[] = [
  { id:1, name:'高频ERROR', type:'level', threshold:5, enabled:true },
  { id:2, name:'异常流量', type:'count', threshold:200, enabled:false },
  { id:3, name:'关键词命中', type:'keyword', threshold:0, enabled:true, keywords:['timeout', 'oom', 'failed'] }
]

function loadRules(): AlertRule[] {
  try {
    const raw = localStorage.getItem(RULES_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length) return parsed
    }
  } catch { /* ignore corrupt cache */ }
  return DEFAULT_RULES
}

export const useLogStore = defineStore('log', () => {
  const result = ref<AnalysisResult | null>(null)
  const loading = ref(false)
  const searchQuery = ref('')
  // 规则清单与日志类型解耦：切换日志类型不丢失，刷新后从 localStorage 恢复，保证与检测口径一致
  const logType = ref(localStorage.getItem(LOG_TYPE_KEY) || 'nginx')
  const rules = ref<AlertRule[]>(loadRules())

  watch(rules, (v) => localStorage.setItem(RULES_KEY, JSON.stringify(v)), { deep: true })
  watch(logType, (v) => localStorage.setItem(LOG_TYPE_KEY, v))

  async function generate() {
    loading.value=true
    try { const {data} = await axios.post('/api/generate',{type:logType.value,count:1000}) ; result.value=data }
    finally { loading.value=false }
  }

  async function detect() {
    if (!result.value) return
    loading.value=true
    try { const {data} = await axios.post('/api/detect',{logs:result.value.logs,rules:rules.value.filter(r=>r.enabled),query:searchQuery.value}) ; result.value=data }
    finally { loading.value=false }
  }

  async function batchUpdateRules(operation: BatchOperation, ruleIds: number[], keywords: string[], batchId: string): Promise<BatchRulesResponse> {
    const { data } = await axios.post('/api/rules/batch', {
      rules: rules.value,
      operation,
      rule_ids: ruleIds,
      keywords,
      batch_id: batchId
    })
    // 以服务端回传的规则状态为准，保证与后续检测结果口径一致
    rules.value = data.rules
    return data as BatchRulesResponse
  }

  async function releaseBatch(batchId: string): Promise<void> {
    try { await axios.delete(`/api/rules/batch/${batchId}`) }
    catch { /* 批次已失效不影响后续下发 */ }
  }

  return { result, loading, searchQuery, logType, rules, generate, detect, batchUpdateRules, releaseBatch }
})
