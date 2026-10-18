import React, { useEffect, useRef } from 'react';
import { Animated, Text, View } from 'react-native';

import { colors, LEVEL, useScale } from '../theme';
import type { Event } from '../types';

const TONE: Record<string, string> = {
  ESCALATED: colors.red,
  SUBMITTED: '#a78bfa',
  APPROVED: colors.green,
  COMMITTED: colors.green,
  CLOSED: colors.green,
};

/** The latest event across the operation, as the desk speaker announces it; it pulses once
 * when something new lands, so the room notices without anyone watching the screen. */
export const LatestRail = ({ events }: { events: Event[] }) => {
  const s = useScale();
  const pulse = useRef(new Animated.Value(0)).current;
  const newest = events[0];
  const seen = useRef<string | null>(null);

  useEffect(() => {
    const key = newest ? `${newest.disruption_id}:${newest.event}:${newest.at}` : null;
    if (key && seen.current !== null && key !== seen.current) {
      pulse.setValue(1);
      Animated.timing(pulse, { toValue: 0, duration: 2400, useNativeDriver: false }).start();
    }
    seen.current = key;
  }, [newest, pulse]);

  if (!newest) return null;
  const tone = TONE[newest.event] ?? (newest.severity ? LEVEL[newest.severity].dot : colors.sky);
  const background = pulse.interpolate({ inputRange: [0, 1], outputRange: [colors.panel, '#1c2c4a'] });
  return (
    <Animated.View
      style={{
        marginTop: s(18),
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: background,
        borderRadius: s(12),
        borderWidth: s(1),
        borderColor: colors.line,
        borderLeftWidth: s(6),
        borderLeftColor: tone,
        paddingVertical: s(10),
        paddingHorizontal: s(18),
      }}>
      <Text style={{ color: colors.faint, fontSize: s(16), marginRight: s(14) }}>{newest.at.slice(11, 16)}Z</Text>
      <Text style={{ color: tone, fontSize: s(18), fontWeight: '700', marginRight: s(14) }}>{newest.title}</Text>
      <Text style={{ color: colors.text, fontSize: s(18), flex: 1 }} numberOfLines={1}>
        {newest.text.replace(/^(High|Medium|Low) severity\. /, '')}
      </Text>
      {events.length > 1 && (
        <View style={{ marginLeft: s(14) }}>
          <Text style={{ color: colors.faint, fontSize: s(15) }}>
            then {events[1].at.slice(11, 16)}Z {events[1].title.toLowerCase()}
          </Text>
        </View>
      )}
    </Animated.View>
  );
};
