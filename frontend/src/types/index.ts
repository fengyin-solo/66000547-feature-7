export interface LogEntry { id: number; timestamp: string; level: string; source: string; message: string; raw: string }
export interface TimeWindow { start: number; end: number; count: number; levels: Record<string,number>; sources: Record<string,number> }
export interface AnomalyScore { windowIndex: number; sigmaScore: number; iqrScore: number; isAnomaly: boolean; timestamp: string }
export interface AlertRule { id: number; name: string; type: string; threshold: number; enabled: boolean; keywords: string[] }
export interface Alert { id: number; ruleName: string; severity: string; message: string; timestamp: string }
export interface AnalysisResult { logs: LogEntry[]; windows: TimeWindow[]; anomalies: AnomalyScore[]; alerts: Alert[]; totalLogs: number }

export type BatchAction = 'enable' | 'disable' | 'set_keywords'

export interface BatchRuleResult {
  ruleId: number
  ruleName: string
  success: boolean
  changed: boolean
  reason: string
}

export interface BatchResponse {
  batchId: string
  action: BatchAction
  idempotent: boolean
  results: BatchRuleResult[]
  successCount: number
  failureCount: number
  duplicatedIds: number[]
  rules: AlertRule[]
}
