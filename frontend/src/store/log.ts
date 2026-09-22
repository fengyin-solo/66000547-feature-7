import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import type { AnalysisResult, AlertRule, BatchAction, BatchResponse } from '@/types'

const LS_RULES = 'lad:rules'
const LS_LOGTYPE = 'lad:logType'
const LS_QUERY = 'lad:searchQuery'
const LS_RESULT = 'lad:result'
const LS_FINGERPRINT = 'lad:resultFingerprint'

const DEFAULT_RULES: AlertRule[] = [
  { id: 1, name: '高频ERROR', type: 'level', threshold: 5, enabled: true, keywords: [] },
  { id: 2, name: '异常流量', type: 'count', threshold: 200, enabled: false, keywords: [] },
  { id: 3, name: '关键词命中', type: 'keyword', threshold: 0, enabled: true, keywords: ['timeout', 'failed', 'exhausted', 'error'] }
]

function readLS<T>(key: string): T | null {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : null } catch { return null }
}
function writeLS(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* 容量不足等情况忽略 */ }
}

/** 口径指纹：参与检测的启用规则集合（id/类型/阈值/词表） */
export function rulesFingerprint(rules: AlertRule[]): string {
  const active = rules
    .filter(r => r.enabled)
    .map(r => ({ id: r.id, type: r.type, threshold: r.threshold, keywords: [...r.keywords].sort() }))
    .sort((a, b) => a.id - b.id)
  return JSON.stringify(active)
}

export function newBatchId(): string {
  return `batch-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export const useLogStore = defineStore('log', () => {
  const result = ref<AnalysisResult | null>(readLS<AnalysisResult>(LS_RESULT))
  const loading = ref(false)
  const searchQuery = ref(readLS<string>(LS_QUERY) || '')
  const logType = ref(readLS<string>(LS_LOGTYPE) || 'nginx')
  const storedRules = readLS<AlertRule[]>(LS_RULES)
  const rules = ref<AlertRule[]>(
    storedRules && storedRules.length
      ? storedRules.map(r => ({ ...r, keywords: r.keywords || [] }))
      : DEFAULT_RULES.map(r => ({ ...r }))
  )
  /** 刷新前最后一次检测对应的规则口径；与当前规则不一致时提示重新检测，避免静默错配 */
  const resultFingerprint = ref<string>(readLS<string>(LS_FINGERPRINT) || '')
  const caliberStale = ref(false)

  const currentFingerprint = computed(() => rulesFingerprint(rules.value))

  function persistRules() { writeLS(LS_RULES, rules.value) }

  /** 启动时与服务端规则对账：服务端为权威口径；不一致则更新并标记检测结果过期 */
  async function syncRules() {
    try {
      const { data } = await axios.get('/api/rules')
      const serverRules = (data.rules || []) as AlertRule[]
      if (!serverRules.length) return
      rules.value = serverRules.map(r => ({ ...r, keywords: r.keywords || [] }))
      persistRules()
      if (result.value && resultFingerprint.value && currentFingerprint.value !== resultFingerprint.value) {
        caliberStale.value = true
      }
    } catch {
      // 后端不可用时沿用本地持久化的规则，保证刷新后清单与口径不变
    }
  }

  async function generate() {
    loading.value = true
    try {
      const { data } = await axios.post('/api/generate', {
        type: logType.value, count: 1000, rules: enabledPayload()
      })
      result.value = data
      resultFingerprint.value = currentFingerprint.value
      caliberStale.value = false
      writeLS(LS_RESULT, data)
      writeLS(LS_FINGERPRINT, resultFingerprint.value)
    } finally { loading.value = false }
  }

  function enabledPayload() {
    return rules.value.filter(r => r.enabled)
  }

  async function detect() {
    if (!result.value) return
    loading.value = true
    try {
      const { data } = await axios.post('/api/detect', {
        logs: result.value.logs, rules: enabledPayload(), query: searchQuery.value
      })
      result.value = data
      resultFingerprint.value = currentFingerprint.value
      caliberStale.value = false
      writeLS(LS_RESULT, data)
      writeLS(LS_FINGERPRINT, resultFingerprint.value)
    } finally { loading.value = false }
  }

  /**
   * 批量下发规则操作。
   * batchId 在打开批次对话框时生成：同一批次重复提交只生效一次（服务端幂等回放），
   * 取消批次时释放该 batchId，之后可用新批次重新下发。
   */
  async function submitBatch(action: BatchAction, ruleIds: number[], keywords: string[], batchId: string): Promise<BatchResponse> {
    const beforeFingerprint = currentFingerprint.value
    const { data } = await axios.post('/api/rules/batch', {
      batch_id: batchId, action, rule_ids: ruleIds,
      keywords: action === 'set_keywords' ? keywords : []
    })
    rules.value = (data.rules as AlertRule[]).map(r => ({ ...r, keywords: r.keywords || [] }))
    persistRules()
    // 口径确实变化时：重新检测成功后 detect() 会复位；重检失败则提示用户手动刷新结果
    if (result.value && beforeFingerprint !== currentFingerprint.value) caliberStale.value = true
    return data as BatchResponse
  }

  /** 取消批次：释放幂等令牌，允许同一组规则重新下发 */
  async function cancelBatch(batchId: string) {
    try { await axios.delete(`/api/rules/batch/${encodeURIComponent(batchId)}`) } catch { /* 忽略 */ }
  }

  function persistUiState() {
    writeLS(LS_LOGTYPE, logType.value)
    writeLS(LS_QUERY, searchQuery.value)
  }

  return {
    result, loading, searchQuery, logType, rules,
    resultFingerprint, caliberStale, currentFingerprint,
    syncRules, generate, detect, submitBatch, cancelBatch, persistUiState
  }
})
