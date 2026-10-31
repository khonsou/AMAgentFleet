export interface DioramaHandle {
  sync(snapshot: ReturnType<typeof import('./model').toDioramaSnapshot>): void
  playEvent(event: unknown): void
  setView(view: string): void
  focus(type: string, id: string): void
  resize(): void
  dispose(): void
  setLayer(name: string, visible: boolean): void
  setTour(enabled: boolean): void
  pauseTour(): void
  resumeTour(): void
  projectSelectable(type: string, id: string): { x: number; y: number } | null
  getStats(): { agents: number; projects: number; calls: number; triangles: number; autoRotate: boolean; zoom: number }
}

export function createWorld(
  canvas: HTMLCanvasElement,
  labelRoot: HTMLElement,
  callbacks?: { onSelect?: (type: string, id: string) => void; onInteract?: () => void },
): DioramaHandle
