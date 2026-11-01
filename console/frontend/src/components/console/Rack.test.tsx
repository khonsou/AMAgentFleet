import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import Rack from './Rack'

describe('Console 导航语义', () => {
  it('Playground 是主页，Task Board 是独立任务看板', () => {
    render(<MemoryRouter><Rack /></MemoryRouter>)
    expect(screen.getByRole('link', { name: 'PLAYGROUND' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'TASK BOARD' })).toHaveAttribute('href', '/tasks')
    expect(screen.queryByRole('link', { name: 'FLEET' })).toBeNull()
  })
})
