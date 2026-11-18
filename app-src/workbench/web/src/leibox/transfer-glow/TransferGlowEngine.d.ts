import type { MotionOptions, MotionController } from "./motion-runtime";
export type TransferGlowOptions = MotionOptions & { open?: boolean; amount?: string; account?: string; recipient?: string; receivedAmount?: string; duration?: number; onComplete?: () => void; onOpenChange?: (open: boolean) => void };
export function mountTransferGlow(target: HTMLElement, options?: TransferGlowOptions): Omit<MotionController, "update"> & { update(options: TransferGlowOptions): void };
