import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import { colors, LEVEL, useScale } from '../theme';
import type { DeskLoad } from '../types';

/** A controller's desk on the left of the board. Focus it with the D-pad and press OK to show
 * only that desk's disruptions; OK again shows every desk. */
export const DeskButton = ({
  desk,
  selected,
  onPress,
}: {
  desk: DeskLoad;
  selected: boolean;
  onPress: () => void;
}) => {
  const [focused, setFocused] = useState(false);
  const s = useScale();
  return (
    <Pressable
      onPress={onPress}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={`${desk.controller}, ${desk.open} open. ${selected ? 'Showing only this desk.' : 'Press OK to show only this desk.'}`}
      style={{
        backgroundColor: focused ? colors.panelHigh : selected ? '#132b45' : colors.panel,
        borderRadius: s(14),
        borderWidth: s(2),
        borderColor: focused ? colors.sky : selected ? '#2f6a95' : colors.line,
        paddingVertical: s(10),
        paddingHorizontal: s(18),
        marginBottom: s(12),
        flexDirection: 'row',
        alignItems: 'center',
      }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: colors.text, fontSize: s(21), fontWeight: '600' }} numberOfLines={1}>
          {desk.controller}
        </Text>
        <Text style={{ color: selected ? colors.sky : colors.muted, fontSize: s(15), marginTop: s(2) }} numberOfLines={1}>
          {desk.desk_id.replace('-', ' ')}
          {selected ? '  ·  showing this desk' : desk.submitted ? `  ·  ${desk.submitted} awaiting approval` : ''}
        </Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={{ color: colors.text, fontSize: s(30), fontWeight: '600' }}>{desk.open}</Text>
        {(desk.escalated > 0 || desk.high > 0) && (
          <Text style={{ color: desk.escalated ? colors.red : LEVEL.high.text, fontSize: s(14), fontWeight: '600' }}>
            {desk.escalated ? `${desk.escalated} escalated` : `${desk.high} high`}
          </Text>
        )}
      </View>
    </Pressable>
  );
};

/** "All desks": back to the whole room. */
export const AllDesksButton = ({ selected, onPress }: { selected: boolean; onPress: () => void }) => {
  const [focused, setFocused] = useState(false);
  const s = useScale();
  return (
    <Pressable
      onPress={onPress}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      style={{
        backgroundColor: focused ? colors.panelHigh : selected ? '#132b45' : 'transparent',
        borderRadius: s(12),
        borderWidth: s(2),
        borderColor: focused ? colors.sky : selected ? '#2f6a95' : colors.line,
        paddingVertical: s(6),
        paddingHorizontal: s(18),
        marginBottom: s(12),
      }}>
      <Text style={{ color: selected ? colors.sky : colors.muted, fontSize: s(17), fontWeight: '600' }}>
        All desks{selected ? '  ·  showing' : ''}
      </Text>
    </Pressable>
  );
};
