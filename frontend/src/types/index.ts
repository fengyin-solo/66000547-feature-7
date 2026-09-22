export interface LogEntry { id: number; timestamp: string; level: string; source: string; message: string; raw: string }
export interface TimeWindow { start: number; end: number; count: number; levels: Record<string,number>; sources: Record<string,number> }
export interface AnomalyScore { windowIndex: number; sigmaScore: number; iqrScore: number; isAnomaly: boolean; timestamp: string }
export interface AlertRule { id: number; name: string; type: string; threshold: number; enabled: boolean; keywords?: string[] }
export interface Alert { id: number; ruleName: string; severity: string; message: string; timestamp: string }
export type BatchOperation = 'enable' | 'disable' | 'set_keywords'
export interface BatchRuleResult {
  ruleId: number
  ruleName: string
  success: boolean
  action: string
  changed?: boolean
  message: string
}
export interface BatchRulesResponse {
  batchId: string
  idempotent: boolean
  rules: AlertRule[]
  results: BatchRuleResult[]
  successCount: number
  failCount: number
}
export interface AnalysisResult { logs: LogEntry[]; windows: TimeWindow[]; anomalies: AnomalyScore[]; alerts: Alert[]; totalLogs: number }
