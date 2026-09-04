/**
 * ResizableTable —— 支持鼠标拖动调节列宽的 antd Table 封装。
 *
 * 用法与 antd <Table> 完全一致（props 全部透传），唯一区别：
 *   1) 每个表头单元格右侧出现竖线拖柄，按住左右拖动即可调该列宽
 *   2) 内部 tableLayout="fixed"：列宽完全由 column.width 驱动
 *      （没写 width 的列自动弹性平分剩余宽度，可当「吸收余量」列）
 *   3) 拖过的宽度只存在本组件 state（切页/刷新还原默认），不落 localStorage
 *
 * 零新依赖：pointer 事件 + document 级监听实现，不引入 react-resizable。
 */
import React, { useCallback, useMemo, useRef, useState } from 'react'
import { Table } from 'antd'

const MIN_WIDTH = 60

// 模块级注入一次样式（纯浏览器项目，无 SSR）
if (typeof document !== 'undefined' && !document.getElementById('hbjf-resizable-style')) {
  const style = document.createElement('style')
  style.id = 'hbjf-resizable-style'
  style.textContent = `
    .hbjf-col-resize-handle {
      position: absolute; right: 0; top: 0; height: 100%; width: 7px;
      cursor: col-resize; z-index: 3; user-select: none;
    }
    .hbjf-col-resize-handle:hover,
    .hbjf-col-resize-handle:active { background: rgba(24, 144, 255, 0.28); }
    body.hbjf-col-resizing, body.hbjf-col-resizing * {
      cursor: col-resize !important; user-select: none !important;
    }
  `
  document.head.appendChild(style)
}

function colKeyOf(col, index) {
  if (col && (col.key || col.dataIndex)) return String(col.key || col.dataIndex)
  return `__col_${index}`
}

export default function ResizableTable({ columns, ...rest }) {
  const [userWidths, setUserWidths] = useState({}) // colKey -> 用户拖出来的宽
  const drag = useRef(null)                         // { key, startX, startWidth }

  const startDrag = useCallback((key, e, startWidth) => {
    e.preventDefault()
    drag.current = { key, startX: e.clientX, startWidth }
    document.body.classList.add('hbjf-col-resizing')
    const move = ev => {
      const d = drag.current
      if (!d) return
      const next = Math.max(MIN_WIDTH, d.startWidth + ev.clientX - d.startX)
      setUserWidths(w => (w[d.key] === next ? w : { ...w, [d.key]: next }))
    }
    const up = () => {
      drag.current = null
      document.body.classList.remove('hbjf-col-resizing')
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
  }, [])

  const finalColumns = useMemo(
    () => (columns || []).map((col, i) => {
      const key = colKeyOf(col, i)
      const width = userWidths[key] !== undefined ? userWidths[key] : col.width
      return {
        ...col,
        width,
        onHeaderCell: () => ({
          width,
          // 拖拽起点宽度：优先取当前列宽；无 width 的弹性列读 DOM 实际渲染宽度
          onResize: (e, domWidth) => startDrag(key, e, domWidth || width || 160),
        }),
      }
    }),
    [columns, userWidths, startDrag]
  )

  const resizableCell = useCallback(
    props => {
      const { onResize, children, ...thProps } = props
      const thRef = useRef(null)
      return (
        <th
          {...thProps}
          ref={thRef}
          style={{ position: 'relative', ...(thProps.style || {}) }}
        >
          {children}
          {onResize ? (
            <span
              className="hbjf-col-resize-handle"
              title="拖动调整列宽"
              onPointerDown={e => {
                e.stopPropagation() // 不影响列头排序/筛选点击
                onResize(e, thRef.current ? thRef.current.offsetWidth : 0)
              }}
            />
          ) : null}
        </th>
      )
    },
    []
  )

  return (
    <Table
      {...rest}
      columns={finalColumns}
      tableLayout="fixed"
      components={{ header: { cell: resizableCell } }}
    />
  )
}
