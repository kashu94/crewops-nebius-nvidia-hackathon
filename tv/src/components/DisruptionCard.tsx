import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import { AIRLINE, colors, LEVEL, rupees, useScale, waited } from '../theme';
import type { Card } from '../types';

const STATUS: Record<string, string> = {
  OPTIONS_READY: 'Needs a plan',
  SUBMITTED: 'Waiting for approval',
  REPORTED: 'Being analysed',
};

/** One open disruption, focusable with the D-pad: a sky outline when focused. */
export const DisruptionCard = ({ card, first, onPress }: { card: Card; first: boolean; onPress: () => void }) => {
  const [focused, setFocused] = useState(false);
  const s = useScale();
  const level = card.severity ? LEVEL[card.severity] : null;
  const cover = card.cover[0];
  const accent = card.escalated ? colors.red : (level?.dot ?? colors.line);
  return (
    <Pressable
      onPress={onPress}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      hasTVPreferredFocus={first}
      accessibilityRole="button"
      accessibilityLabel={`${card.what}. ${card.severity ?? ''} severity. ${STATUS[card.status] ?? card.status}`}
      style={{
        width: '49%',
        marginBottom: s(16),
        padding: s(20),
        borderRadius: s(16),
        backgroundColor: focused ? colors.panelHigh : card.escalated ? colors.redDeep : colors.panel,
        borderWidth: s(2),
        borderColor: focused ? colors.sky : card.escalated ? '#6b1f28' : colors.line,
        borderLeftWidth: s(6),
        borderLeftColor: accent,
      }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: s(10) }}>
        {level && (
          <View style={{ flexDirection: 'row', alignItems: 'center', marginRight: s(14) }}>
            <View style={{ width: s(10), height: s(10), borderRadius: s(5), backgroundColor: level.dot, marginRight: s(7) }} />
            <Text style={{ color: level.text, fontSize: s(18), fontWeight: '600' }}>{level.label}</Text>
          </View>
        )}
        {card.escalated && (
          <Text style={{ color: '#fff', backgroundColor: colors.red, fontSize: s(15), fontWeight: '700', paddingHorizontal: s(8), paddingVertical: s(2), borderRadius: s(6), marginRight: s(10) }}>
            Escalated
          </Text>
        )}
        {card.airlines.map((a) => (
          <Text key={a} style={{ color: AIRLINE[a]?.color ?? colors.muted, fontSize: s(16), fontWeight: '700', marginRight: s(8) }}>
            {AIRLINE[a]?.name ?? a}
          </Text>
        ))}
        <Text style={{ marginLeft: 'auto', color: colors.muted, fontSize: s(16) }}>{waited(card.waiting_minutes)}</Text>
      </View>
      <Text style={{ color: colors.text, fontSize: s(24), fontWeight: '600', lineHeight: s(32) }} numberOfLines={2}>
        {card.what}
      </Text>
      <Text style={{ color: colors.muted, fontSize: s(17), marginTop: s(8) }} numberOfLines={1}>
        {STATUS[card.status] ?? card.status}
        {card.desk ? `  ·  ${card.desk.controller}` : ''}
      </Text>
      <Text style={{ color: colors.muted, fontSize: s(16), marginTop: s(4) }} numberOfLines={1}>
        Touches {card.impact.flights} flight{card.impact.flights === 1 ? '' : 's'} ·{' '}
        {card.impact.passengers.toLocaleString('en-IN')} passengers booked
      </Text>
      {cover && (
        <Text style={{ color: '#9be7c6', fontSize: s(17), marginTop: s(4) }} numberOfLines={1}>
          {cover.seat}  →  {cover.who || cover.how}
          {cover.who ? `, ${cover.how.toLowerCase()}` : ''}  {rupees(cover.cost_inr)}
        </Text>
      )}
    </Pressable>
  );
};
