import { render, screen, fireEvent } from '@testing-library/react'
import { beforeAll, expect, test, vi } from 'vitest'
import ScanScreen from './ScanScreen'

// jsdom does not implement object URLs; the preview <img> creates and revokes them.
beforeAll(() => {
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
})

function addFront() {
  fireEvent.change(screen.getByLabelText(/photograph card front/i), {
    target: { files: [new File(['x'], 'front.jpg', { type: 'image/jpeg' })] },
  })
}

test('scan button is disabled until a front photo is chosen', () => {
  render(<ScanScreen onSubmit={() => {}} busy={false} />)
  const button = screen.getByRole('button', { name: /scan card/i }) as HTMLButtonElement
  expect(button.disabled).toBe(true)

  addFront()
  expect(button.disabled).toBe(false)
  expect(screen.getByAltText('card front')).toBeDefined()
})

test('busy prop shows Scanning… and disables the button', () => {
  render(<ScanScreen onSubmit={() => {}} busy />)
  const button = screen.getByRole('button', { name: /scanning/i }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
})

test('tapping a price chip submits that price', () => {
  const onSubmit = vi.fn()
  render(<ScanScreen onSubmit={onSubmit} busy={false} />)
  addFront()

  const chip = screen.getByRole('button', { name: '$20' })
  fireEvent.click(chip)
  expect(chip.getAttribute('aria-pressed')).toBe('true')

  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(onSubmit.mock.calls[0][2]).toBe(20)
})

test('tapping the selected chip again deselects it and submits null', () => {
  const onSubmit = vi.fn()
  render(<ScanScreen onSubmit={onSubmit} busy={false} />)
  addFront()

  const chip = screen.getByRole('button', { name: '$50' })
  fireEvent.click(chip)
  fireEvent.click(chip)
  expect(chip.getAttribute('aria-pressed')).toBe('false')

  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
  expect(onSubmit.mock.calls[0][2]).toBeNull()
})

test('selecting a chip replaces a previously selected chip', () => {
  const onSubmit = vi.fn()
  render(<ScanScreen onSubmit={onSubmit} busy={false} />)
  addFront()

  fireEvent.click(screen.getByRole('button', { name: '$5' }))
  fireEvent.click(screen.getByRole('button', { name: '$100' }))
  expect(screen.getByRole('button', { name: '$5' }).getAttribute('aria-pressed')).toBe('false')
  expect(screen.getByRole('button', { name: '$100' }).getAttribute('aria-pressed')).toBe('true')

  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
  expect(onSubmit.mock.calls[0][2]).toBe(100)
})

test('Other… reveals and focuses the numeric input; typed value wins and chips deselect', () => {
  const onSubmit = vi.fn()
  render(<ScanScreen onSubmit={onSubmit} busy={false} />)
  addFront()

  const input = screen.getByLabelText(/asking price/i) as HTMLInputElement
  expect(input.hidden).toBe(true)

  fireEvent.click(screen.getByRole('button', { name: '$10' }))
  fireEvent.click(screen.getByRole('button', { name: /other/i }))
  expect(input.hidden).toBe(false)
  expect(document.activeElement).toBe(input)

  fireEvent.change(input, { target: { value: '42' } })
  expect(screen.getByRole('button', { name: '$10' }).getAttribute('aria-pressed')).toBe('false')

  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
  expect(onSubmit.mock.calls[0][2]).toBe(42)
})

test('selecting a chip after typing a custom value clears the typed value', () => {
  const onSubmit = vi.fn()
  render(<ScanScreen onSubmit={onSubmit} busy={false} />)
  addFront()

  fireEvent.click(screen.getByRole('button', { name: /other/i }))
  const input = screen.getByLabelText(/asking price/i) as HTMLInputElement
  fireEvent.change(input, { target: { value: '42' } })

  fireEvent.click(screen.getByRole('button', { name: '$10' }))
  expect(input.hidden).toBe(true)
  expect(input.value).toBe('')

  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
  expect(onSubmit.mock.calls[0][2]).toBe(10)
})

test('scan CTA lives in a sticky bottom bar', () => {
  render(<ScanScreen onSubmit={() => {}} busy={false} />)
  const button = screen.getByRole('button', { name: /scan card/i })
  expect(button.closest('.scan-cta')).not.toBeNull()
})
