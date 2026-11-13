<template>
  <section class="page" data-module="dag">
    <header class="page-head">
      <div>
        <h2>任务依赖编排管理</h2>
        <p class="page-desc">维护任务依赖DAG的节点关系、执行条件与失败策略，为任务依赖编排提供可审计的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记依赖图节点</button>
        <button class="btn" type="button" @click="exportRows">导出任务依赖编排清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <button v-if="column === '节点名称'" class="link" type="button" @click="openDrawer(row)">
              {{ row[column] ?? '—' }}
            </button>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!canRun(action, row)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无任务依赖编排数据，可先登记依赖图节点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条任务依赖编排记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <aside v-if="drawerEntry" class="drawer" data-testid="node-drawer">
      <header class="drawer-head">
        <h3>节点状态抽屉：{{ drawerEntry['节点编号'] }}</h3>
        <button class="btn ghost" type="button" @click="closeDrawer">关闭</button>
      </header>
      <dl class="drawer-fields">
        <template v-for="column in columns" :key="column">
          <dt>{{ column }}</dt>
          <dd>{{ drawerEntry[column] ?? '—' }}</dd>
        </template>
      </dl>
      <div class="row-actions drawer-actions">
        <button
          v-for="action in actions"
          :key="action"
          class="btn"
          type="button"
          :disabled="!canRun(action, drawerEntry)"
          @click="runAction(action, drawerEntry)"
        >
          {{ action }}
        </button>
      </div>
      <h4>处理回执（SLA风险重算结论）</h4>
      <ul v-if="drawerReceipts.length" class="receipt-list">
        <li v-for="receipt in drawerReceipts" :key="receipt['回执编号']">
          <strong>{{ receipt['回执编号'] }}</strong>
          {{ receipt['动作'] }}：{{ receipt['变更前状态'] }} → {{ receipt['变更后状态'] }}
          <p>{{ receipt['SLA风险重算结论'] }}</p>
          <small>{{ receipt['时间'] }}</small>
        </li>
      </ul>
      <p v-else class="empty-state">暂无处理回执</p>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dag'
const columns = ["节点编号", "节点名称", "上游节点", "下游节点", "执行条件", "超时分钟", "失败策略", "节点状态"]
const actions = ["启动节点", "暂停节点", "归档节点"]
const statuses = ["草稿", "运行中", "已暂停", "已完成"]
// 状态机：每个动作只允许在对应前置状态触发，与后端单向推进规则保持一致
const actionPrecondition: Record<string, string> = {
  启动节点: '草稿',
  暂停节点: '运行中',
  归档节点: '已暂停',
}
const stats = [{"label": "依赖图节点总数", "value": 0}, {"label": "运行中节点", "value": 0}, {"label": "已暂停节点", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const drawerEntry = ref<Row | null>(null)

const drawerReceipts = computed(() => {
  const entry = drawerEntry.value
  if (!entry) return [] as Record<string, string>[]
  const receipts = (entry as Record<string, unknown>)['处理回执']
  return Array.isArray(receipts) ? (receipts as Record<string, string>[]) : []
})

function canRun(action: string, row: Row): boolean {
  return row['节点状态'] === actionPrecondition[action]
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '依赖图节点登记入口尚未接入审批流'
}

async function openDrawer(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('节点状态读取失败')
    }
    drawerEntry.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点状态读取失败'
  }
}

function closeDrawer() {
  drawerEntry.value = null
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? payload.detail ?? '任务依赖编排动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? '动作已生效'
    await reload()
    if (drawerEntry.value) {
      await openDrawer(drawerEntry.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务依赖编排操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('依赖图节点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务依赖编排列表读取失败'
  }
}

onMounted(reload)
</script>
