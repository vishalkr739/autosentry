import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge Tailwind classes, as cloud-portal's shadcn components do. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
