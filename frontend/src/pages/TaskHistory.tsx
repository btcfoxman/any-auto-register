import { useCallback, useEffect, useState, type KeyboardEvent } from 'react'
import { getPlatforms } from '@/lib/app-data'
import { apiFetch } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { TaskLogPanel } from '@/components/tasks/TaskLogPanel'
import { getTaskStatusText, TASK_STATUS_VARIANTS } from '@/lib/tasks'
import { RefreshCw, Activity, CheckCircle2, AlertTriangle, Clock3, ChevronDown, FileText, X } from 'lucide-react'

type PlatformOption = {
  name: string
  display_name: string
}

type TaskItem = {
  id: string
  task_id?: string
  type?: string
  platform?: string
  status: string
  progress?: string
  success?: number
  error_count?: number
  error?: string
  created_at?: string | null
}

type TasksListResponse = {
  items?: TaskItem[]
}

function shortId(id: string) {
  if (!id) return '-'
  return id.length > 12 ? '...' + id.slice(-8) : id
}

function formatError(error: string | null | undefined): string {
  if (!error) return ''
  // Try to extract a readable message from JSON-like strings
  try {
    if (error.startsWith('{') || error.startsWith('[')) {
      const parsed = JSON.parse(error)
      if (parsed.message) return parsed.message
      if (parsed.error) return parsed.error
      if (Array.isArray(parsed.errors) && parsed.errors.length > 0) {
        const first = parsed.errors[0]
        return first.message || first.kind || JSON.stringify(first).slice(0, 80)
      }
    }
  } catch {
    // not JSON
  }
  // Truncate long strings
  return error.length > 100 ? error.slice(0, 100) + '...' : error
}

export default function TaskHistory() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [platform, setPlatform] = useState('')
  const [status, setStatus] = useState('')
  const [platforms, setPlatforms] = useState<PlatformOption[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedTask, setSelectedTask] = useState<TaskItem | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (platform) params.set('platform', platform)
      if (status) params.set('status', status)
      const data = await apiFetch(`/tasks?${params}`) as TasksListResponse
      setTasks(Array.isArray(data.items) ? data.items : [])
    } finally {
      setLoading(false)
    }
  }, [platform, status])

  useEffect(() => {
    getPlatforms()
      .then((data) => setPlatforms(Array.isArray(data) ? data as PlatformOption[] : []))
      .catch(() => setPlatforms([]))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const succeeded = tasks.filter((t) => t.status === 'succeeded').length
  const failed = tasks.filter((t) => t.status === 'failed').length
  const running = tasks.filter((t) =>
    ['running', 'claimed', 'pending', 'cancel_requested'].includes(t.status)
  ).length

  const metricCards = [
    { label: '任务数', value: tasks.length, icon: Activity, tone: 'text-[var(--accent)]' },
    { label: '成功', value: succeeded, icon: CheckCircle2, tone: 'text-emerald-500' },
    { label: '失败', value: failed, icon: AlertTriangle, tone: 'text-red-500' },
    { label: '进行中', value: running, icon: Clock3, tone: 'text-amber-500' },
  ]

  const openTask = (task: TaskItem) => {
    setSelectedTask(task)
  }

  const handleTaskKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, task: TaskItem) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openTask(task)
    }
  }

  const handleTaskDone = (taskId: string, nextStatus: string) => {
    setTasks(prev => prev.map(item => item.id === taskId ? { ...item, status: nextStatus } : item))
    setSelectedTask((current) =>
      current?.id === taskId ? { ...current, status: nextStatus } : current
    )
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--text-primary)]">任务记录</h1>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {/* Metrics */}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        {metricCards.map(({ label, value, icon: Icon, tone }) => (
          <div
            key={label}
            className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3"
          >
            <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--chip-bg)] ${tone}`}>
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider">{label}</div>
              <div className="text-lg font-semibold text-[var(--text-primary)]">{value}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Filters — inline with table header */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] overflow-hidden">
        <div className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <span className="text-sm font-medium text-[var(--text-primary)]">最近任务</span>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <div className="relative">
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="h-8 appearance-none rounded-md border border-[var(--border)] bg-[var(--bg-input)] pl-3 pr-7 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] focus:border-[var(--accent)]"
              >
                <option value="">全部平台</option>
                {platforms.map((item) => (
                  <option key={item.name} value={item.name}>{item.display_name}</option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--text-muted)]" />
            </div>
            <div className="relative">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="h-8 appearance-none rounded-md border border-[var(--border)] bg-[var(--bg-input)] pl-3 pr-7 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] focus:border-[var(--accent)]"
              >
                <option value="">全部状态</option>
                <option value="running">运行中</option>
                <option value="succeeded">成功</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
                <option value="interrupted">已中断</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--text-muted)]" />
            </div>
            {(platform || status) && (
              <button
                onClick={() => { setPlatform(''); setStatus('') }}
                className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)]"
              >
                清除
              </button>
            )}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--bg-pane)]">
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">时间</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">任务 ID</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">平台</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">状态</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">进度</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">成功/失败</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--text-muted)]">错误</th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-sm text-[var(--text-muted)]">
                    暂无任务记录
                  </td>
                </tr>
              )}
              {tasks.map((task) => {
                const success = task.success || 0
                const errorCount = task.error_count || 0
                const total = success + errorCount
                const errorText = formatError(task.error)
                return (
                  <tr
                    key={task.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openTask(task)}
                    onKeyDown={(event) => handleTaskKeyDown(event, task)}
                    className="cursor-pointer border-b border-[var(--border)]/50 outline-none transition-colors hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)] focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--accent)]"
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-[var(--text-muted)]">
                      {task.created_at
                        ? new Date(task.created_at).toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                          })
                        : '-'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <FileText className="h-3.5 w-3.5 text-[var(--text-muted)]" />
                        <span
                          className="font-mono text-xs text-[var(--text-muted)]"
                          title={task.id}
                        >
                          {shortId(task.id)}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="secondary">{task.platform || '-'}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={TASK_STATUS_VARIANTS[task.status] || 'secondary'}>
                        {getTaskStatusText(task.status)}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs text-[var(--text-secondary)]">
                      {task.progress || '-'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {total > 0 ? (
                          <>
                            <div className="flex h-1.5 w-16 overflow-hidden rounded-full bg-[var(--chip-bg)]">
                              {success > 0 && (
                                <div
                                  className="h-full bg-emerald-500 rounded-full"
                                  style={{ width: `${(success / total) * 100}%` }}
                                />
                              )}
                              {errorCount > 0 && (
                                <div
                                  className="h-full bg-red-500 rounded-full"
                                  style={{ width: `${(errorCount / total) * 100}%` }}
                                />
                              )}
                            </div>
                            <span className="text-xs text-[var(--text-muted)] whitespace-nowrap">
                              <span className="text-emerald-500">{success}</span>
                              {' / '}
                              <span className="text-red-500">{errorCount}</span>
                            </span>
                          </>
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">-</span>
                        )}
                      </div>
                    </td>
                    <td className="max-w-[280px] px-4 py-3">
                      {errorText ? (
                        <span
                          className="block truncate text-xs text-red-500 cursor-default"
                          title={task.error || ''}
                        >
                          {errorText}
                        </span>
                      ) : (
                        <span className="text-xs text-[var(--text-muted)]">-</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
      {selectedTask ? (
        <TaskHistoryLogModal
          task={selectedTask}
          onClose={() => setSelectedTask(null)}
          onDone={(nextStatus) => handleTaskDone(selectedTask.id, nextStatus)}
        />
      ) : null}
    </div>
  )
}

function TaskHistoryLogModal({
  task,
  onClose,
  onDone,
}: {
  task: TaskItem
  onClose: () => void
  onDone: (status: string) => void
}) {
  const taskStatus = task.status || 'pending'

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-panel flex w-[min(960px,calc(100vw-32px))] max-w-none flex-col overflow-hidden"
        onClick={event => event.stopPropagation()}
        style={{ maxHeight: '90vh' }}
      >
        <div className="relative overflow-hidden border-b border-[var(--border)] px-6 py-5">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_12%_0%,rgba(9,182,162,0.18),transparent_34%),linear-gradient(90deg,rgba(255,255,255,0.04),transparent)]" />
          <div className="relative flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="mb-2 inline-flex rounded-full border border-[var(--border)] bg-[var(--chip-bg)] px-3 py-1 text-[11px] tracking-[0.12em] text-[var(--text-muted)]">
                {task.type || 'task'}
              </div>
              <h2 className="truncate text-lg font-semibold text-[var(--text-primary)]">执行日志</h2>
              <p className="mt-1 truncate font-mono text-xs text-[var(--text-muted)]">{task.id}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{task.platform || '-'}</Badge>
              <Badge variant={TASK_STATUS_VARIANTS[taskStatus] || 'secondary'}>
                {getTaskStatusText(taskStatus)}
              </Badge>
              <button
                type="button"
                onClick={onClose}
                className="rounded-full border border-[var(--border)] bg-[var(--bg-hover)] p-2 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <TaskLogPanel taskId={task.id} onDone={onDone} />
        </div>
        <div className="flex items-center justify-end border-t border-[var(--border)] px-6 py-3">
          <Button variant="outline" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  )
}
