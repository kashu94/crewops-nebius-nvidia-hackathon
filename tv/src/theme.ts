import { useWindowDimensions } from 'react-native';

import type { Level } from './types';

// Calm and high-contrast, for a screen read from across the room: colour only where it
// carries meaning (severity, escalation), the same colours as the CrewOps web app.
export const colors = {
  bg: '#0a1020',
  panel: '#111a2e',
  panelHigh: '#17233d',
  line: '#22304d',
  text: '#e8eef7',
  muted: '#8b9bb4',
  faint: '#5d6b85',
  sky: '#5cc8f5',
  green: '#3ecf9a',
  amber: '#f5b83d',
  red: '#f05252',
  redDeep: '#3a1016',
};

export const LEVEL: Record<Level, { label: string; text: string; dot: string }> = {
  high: { label: 'High', text: '#ff9b9b', dot: '#f05252' },
  medium: { label: 'Medium', text: '#f7cd6b', dot: '#f5a623' },
  low: { label: 'Low', text: '#7fe0bd', dot: '#2fbf8a' },
};

export const AIRLINE: Record<string, { name: string; color: string }> = {
  DX: { name: 'Deccan Express', color: '#0284c7' },
  KS: { name: 'Konkan Skyways', color: '#ea580c' },
  NG: { name: 'Nilgiri Connect', color: '#059669' },
};

/** Sizes are designed for a 1920-wide screen and scaled to the one the app runs on, so the
 * same layout fits a 4K TV, a 1080p stick or the simulator's smaller window. */
export function useScale(): (px: number) => number {
  const { width } = useWindowDimensions();
  const k = width / 1920;
  return (px: number) => Math.round(px * k);
}

export function waited(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  return h < 48 ? `${h} h ${minutes % 60} min` : `${Math.floor(h / 24)} days`;
}

export function rupees(value: number | null): string {
  return value == null ? '' : `₹${value.toLocaleString('en-IN')}`;
}
