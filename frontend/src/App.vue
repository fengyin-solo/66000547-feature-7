<template>
  <div class="app-root">
    <header class="top-bar">
      <h1>📊 分布式日志聚合与智能异常检测平台</h1>
      <div class="toolbar">
        <el-select v-model="store.logType" size="small" style="width:140px" @change="onTypeChange">
          <el-option v-for="t in ['nginx','apache','json_app','custom']" :key="t" :label="t" :value="t"/>
        </el-select>
        <el-input v-model="store.searchQuery" placeholder="搜索关键词..." size="small" style="width:200px" clearable @change="store.persistUiState()"/>
        <el-button size="small" @click="store.generate()" :loading="store.loading">🔍 生成日志</el-button>
        <el-button size="small" type="warning" @click="store.detect()" :disabled="!store.result">⚠ 检测异常</el-button>
      </div>
    </header>
    <el-alert
      v-if="store.caliberStale"
      class="caliber-bar"
      type="warning" show-icon :closable="false"
      title="规则清单在本次检测结果之后发生过变化，当前告警口径与规则不一致，请点击「检测异常」刷新结果。"/>
    <div class="main-grid">
      <div class="grid-col">
        <LogTable />
      </div>
      <div class="grid-col">
        <AnomalyChart />
        <AlertPanel />
        <RulePanel />
      </div>
    </div>
    <div class="bottom-row">
      <TrendChart />
      <HeatmapChart />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import LogTable from './components/LogTable.vue'
import AnomalyChart from './components/AnomalyChart.vue'
import AlertPanel from './components/AlertPanel.vue'
import RulePanel from './components/RulePanel.vue'
import TrendChart from './components/TrendChart.vue'
import HeatmapChart from './components/HeatmapChart.vue'
import { useLogStore } from './store/log'
const store = useLogStore()

onMounted(() => {
  // 刷新后先与服务端对账规则口径，再判断检测结果是否过期
  store.syncRules()
})

function onTypeChange() {
  // 切换日志类型保留规则清单，仅持久化选择
  store.persistUiState()
}
</script>

<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,monospace;background:#0f172a;color:#e2e8f0}
.app-root{min-height:100vh}
.top-bar{display:flex;justify-content:space-between;align-items:center;padding:10px 20px;background:#1e293b;border-bottom:1px solid #334155}
.top-bar h1{font-size:1.1rem;color:#38bdf8}
.toolbar{display:flex;gap:8px;align-items:center}
.caliber-bar{margin:8px 20px 0}
.main-grid{display:grid;grid-template-columns:1fr 400px;gap:12px;padding:12px 20px;min-height:50vh}
.grid-col{overflow:hidden}
.bottom-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:0 20px 16px}
</style>
