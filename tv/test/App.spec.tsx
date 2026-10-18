import {rupees, waited} from '../src/theme';

describe('waited', () => {
  it('reads minutes, then hours, then days', () => {
    expect(waited(12)).toBe('12 min');
    expect(waited(135)).toBe('2 h 15 min');
    expect(waited(60 * 72)).toBe('3 days');
  });
});

describe('rupees', () => {
  it('groups the Indian way and leaves an unknown cost blank', () => {
    expect(rupees(125000)).toBe('₹1,25,000');
    expect(rupees(null)).toBe('');
  });
});
