import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import { colors, LEVEL, rupees, useScale, waited } from '../theme';
import type { Card } from '../types';

/** One disruption opened with OK: why it is that serious, who has it, and the recommended
 * cover for each seat. Back (or OK on Close) returns to the board. */
export const DetailPanel = ({ card, onClose }: { card: Card; onClose: () => void }) => {
  const [closeFocused, setCloseFocused] = useState(false);
  const s = useScale();
  const level = card.severity ? LEVEL[card.severity] : null;
  const section = { color: colors.sky, fontSize: s(16), fontWeight: '600' as const, letterSpacing: s(1), marginTop: s(24), marginBottom: s(6) };
  const line = { color: colors.text, fontSize: s(22), lineHeight: s(32) };
  return (
    <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(5,9,20,0.85)', alignItems: 'center', justifyContent: 'center' }}>
      <View
        style={{
          width: '62%',
          backgroundColor: colors.panel,
          borderRadius: s(22),
          borderWidth: s(2),
          borderColor: colors.line,
          borderTopWidth: s(6),
          borderTopColor: card.escalated ? colors.red : (level?.dot ?? colors.line),
          padding: s(36),
        }}>
        <Text style={{ color: colors.faint, fontSize: s(16) }}>{card.disruption_id}</Text>
        <Text style={{ color: colors.text, fontSize: s(32), fontWeight: '700', marginTop: s(6) }}>{card.what}</Text>
        <Text style={{ color: colors.muted, fontSize: s(19), marginTop: s(10) }}>
          {level ? `${level.label} severity` : ''}
          {card.escalated ? '  ·  escalated to the duty supervisor' : ''}
          {`  ·  waiting ${waited(card.waiting_minutes)}`}
          {card.desk ? `  ·  ${card.desk.controller}'s desk` : ''}
        </Text>
        <Text style={section}>Impact</Text>
        <Text style={line}>
          {card.impact.flights} flight{card.impact.flights === 1 ? '' : 's'}, {card.impact.passengers.toLocaleString('en-IN')}{' '}
          passengers booked: {card.impact.flight_ids.map((f) => f.split('-')[0]).join(', ')}
        </Text>
        {card.reasons.length > 0 && (
          <>
            <Text style={section}>Why</Text>
            {card.reasons.map((r) => (
              <Text key={r} style={line}>
                •  {r}
              </Text>
            ))}
          </>
        )}
        <Text style={section}>Recommended cover</Text>
        {card.cover.length === 0 ? (
          <Text style={line}>No seat to re-crew: crew control decides with operations.</Text>
        ) : (
          card.cover.map((c) => (
            <Text key={c.seat} style={line}>
              {c.seat}  →  <Text style={{ color: '#9be7c6', fontWeight: '600' }}>{c.who || c.how}</Text>
              {c.who ? `, ${c.how.toLowerCase()}` : ''}  {rupees(c.cost_inr)}
            </Text>
          ))
        )}
        <Text style={{ color: colors.faint, fontSize: s(16), marginTop: s(22) }}>
          The controller submits the plan at their desk; a different person approves it.
        </Text>
        <Pressable
          onPress={onClose}
          hasTVPreferredFocus
          onFocus={() => setCloseFocused(true)}
          onBlur={() => setCloseFocused(false)}
          style={{
            alignSelf: 'flex-start',
            marginTop: s(22),
            backgroundColor: closeFocused ? '#123a52' : colors.line,
            borderRadius: s(12),
            paddingHorizontal: s(26),
            paddingVertical: s(10),
            borderWidth: s(2),
            borderColor: closeFocused ? colors.sky : 'transparent',
          }}>
          <Text style={{ color: colors.text, fontSize: s(20), fontWeight: '600' }}>Close</Text>
        </Pressable>
      </View>
    </View>
  );
};
