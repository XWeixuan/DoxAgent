import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { EmptyState, StatusBadge } from "@/components/dashboard/shared"

describe("dashboard shared components", () => {
  it("renders status labels with stable Chinese text", () => {
    render(<StatusBadge status="normal" label="正常" />)
    expect(screen.getByText("正常")).toBeInTheDocument()
  })

  it("renders empty states in Chinese", () => {
    render(<EmptyState title="暂无消息" description="当前筛选条件下没有消息。" />)
    expect(screen.getByText("暂无消息")).toBeInTheDocument()
    expect(screen.getByText("当前筛选条件下没有消息。")).toBeInTheDocument()
  })
})
