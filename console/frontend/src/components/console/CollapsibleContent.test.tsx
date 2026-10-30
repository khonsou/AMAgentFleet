import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import CollapsibleContent from './CollapsibleContent'

afterEach(cleanup)

describe('CollapsibleContent', () => {
  it('长文本默认显示预览，展开后保留完整内容', async () => {
    const user = userEvent.setup()
    const text = `${'第一行内容\n'.repeat(12)}必须保留的最后一行`
    render(<CollapsibleContent text={text} collapseAfterLines={5} previewLines={3} />)

    expect(screen.queryByText(/必须保留的最后一行/)).toBeNull()
    await user.click(screen.getByRole('button', { name: /展开全文/ }))
    expect(screen.getByText(/必须保留的最后一行/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '收起' }))
    expect(screen.queryByText(/必须保留的最后一行/)).toBeNull()
  })

  it('JSON 默认折叠并显示结构摘要', () => {
    const { container } = render(<CollapsibleContent text={'{"items":[1,2,3],"status":"ok"}'} />)
    const details = container.querySelector('details')
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText(/JSON · 2 个字段/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制 JSON' })).toBeInTheDocument()
  })

  it('Tool 返回默认折叠并保留 Tool 名', () => {
    const { container } = render(<CollapsibleContent text={'{"ok":true}'} kind="tool" label="search_docs" />)
    expect(container.querySelector('details')).not.toHaveAttribute('open')
    expect(screen.getByText(/search_docs · 1 个字段/)).toBeInTheDocument()
  })
})
