"""
Align dice rolls to transcripts.
"""
import argparse
import csv
import json
import logging
import re
from typing import Any, Dict, Generator, List

logger = logging.getLogger(__name__)


CHUNK_SIZE = 4
RE_TIME = re.compile(r'\d{2}:\d{2}:\d{2}')
TIMESTAMP_SPLIT = ' --> '


class Timestamp:
    """A timestamp up to second precision"""
    def __init__(self, hour: int, minute: int, second: int) -> None:
        assert 0 <= second < 60
        assert 0 <= minute < 60
        assert 0 <= hour
        self.hour = hour
        self.minute = minute
        self.second = second

    @classmethod
    def from_string(cls, string: str) -> 'Timestamp':
        """Reads timestamp from a string."""
        hour, minute, second = string.split(':')
        if ',' in second:
            second, _ = second.split(',')
        return cls(int(hour), int(minute), int(second))

    def __int__(self) -> int:
        return self.second + 60 * self.minute + 3600 * self.hour

    def __repr__(self) -> str:
        return f'Timestamp({self.hour:02}:{self.minute:02}:{self.second:02})'

    def __eq__(self, rhs) -> bool:
        return (self.hour == rhs.hour) and (self.minute == rhs.minute) and (self.second == rhs.second)

    def __lt__(self, rhs) -> bool:
        return int(self) < int(rhs)

    def __le__(self, rhs) -> bool:
        return int(self) <= int(rhs)

    def __gt__(self, rhs) -> bool:
        return rhs < self

    def __ge__(self, rhs) -> bool:
        return rhs <= self

    def __add__(self, rhs) -> 'Timestamp':
        if isinstance(rhs, int):
            second = self.second + rhs
            remainder = second // 60
            second = second % 60

            minute = self.minute + remainder
            remainder = minute // 60
            minute = minute % 60

            hour = self.hour + remainder
        elif isinstance(rhs, Timestamp):
            second = self.second + rhs.second
            remainder = second // 60
            second = second % 60

            minute = self.minute + rhs.minute + remainder
            remainder = minute // 60
            minute = minute % 60

            hour = self.hour + rhs.hour + remainder
        else:
            raise TypeError(f'Cannot add {type(rhs)} to a Timestamp')
        return Timestamp(hour, minute, second)

    def __sub__(self, rhs) -> 'Timestamp':
        if isinstance(rhs, int):
            second = self.second - rhs
            remainder = second // 60
            second = second % 60

            minute = self.minute + remainder
            remainder = minute // 60
            minute = minute % 60

            hour = self.hour + remainder
        elif isinstance(rhs, Timestamp):
            second = self.second - rhs.second
            remainder = second // 60
            second = second % 60

            minute = self.minute - rhs.minute + remainder
            remainder = minute // 60
            minute = minute % 60

            hour = self.hour - rhs.hour + remainder
        else:
            raise TypeError(f'Cannot subract {type(rhs)} from a Timestamp')
        return Timestamp(hour, minute, second)


class Caption:
    """Stores caption text + metadata."""
    def __init__(self, index: int, start: Timestamp, end: Timestamp, text:str) -> None:
        self.index = index
        self.start = start
        self.end = end
        self.text = text

    def __repr__(self) -> str:
        return f'Caption(index={self.index}, start={self.start}, end={self.end}, text="{self.text}")'


class DiceRoll:
    """Stores dice roll metadata."""
    def __init__(self, timestamp: Timestamp, roll_type: str, value: int, critical: bool) -> None:
        self.timestamp = timestamp
        self.roll_type = roll_type
        self.value = value
        self.critical = critical

    def __repr__(self) -> str:
        return (
            f'DiceRoll(timestamp={self.timestamp}, roll_type={self.roll_type}, '
            f'value={self.value}, critical={self.critical})'
        )


def read_transcript(fname) -> List[Caption]:
    """Reads a transcript file into a list of captions."""
    transcript: List[Caption] = []

    with open(fname, 'r') as f:
        index = 1
        while True:
            # Get the next line
            try:
                line = next(f)
            except StopIteration:
                break
            else:
                line = line.strip()

            # If it is an index then start a new line of dialogue
            if line == str(index):

                # Create a new caption
                timestamp = next(f)
                start_string, end_string = timestamp.strip().split(TIMESTAMP_SPLIT)
                start_timestamp = Timestamp.from_string(start_string)
                end_timestamp = Timestamp.from_string(end_string)
                text = next(f)
                text = text.strip()
                caption = Caption(index, start_timestamp, end_timestamp, text)

                # Add to list - since captions are objects they will be updated without needing to
                # overwrite.
                transcript.append(caption)

                index += 1

            # Any other non-empty lines are considered part of the current caption's text.
            elif line != '':
                caption.text = ' '.join([caption.text, line])

    return transcript


def read_dice_rolls(fname: str, offset: Timestamp) -> List[DiceRoll]:
    dice_rolls: List[DiceRoll] = []

    with open(fname, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:

            # Extract the timestamp
            time_string = RE_TIME.search(row['Time'])
            if time_string is None:
                continue
            timestamp = Timestamp.from_string(time_string.group(0)) - offset

            # Extract the type of roll
            roll_type = row['Type of Roll']

            # Extract the roll value / whether it is a crit
            value_string = row['Total Value']
            if value_string == 'Nat20':
                value = 20
                critical = True
            elif value_string == 'Nat1':
                value = 1
                critical = True
            else:
                try:
                    value = int(value_string)
                except ValueError:
                    continue
                critical = False

            # Create dice roll
            dice_roll = DiceRoll(timestamp, roll_type, value, critical)
            dice_rolls.append(dice_roll)

    return dice_rolls


def generate_annotations(transcript: List[Caption],
                         dice_rolls: List[DiceRoll],
                         window: int):
    """Generates annotations by combining transcript and dice rolls."""
    transcript_iterator = iter(transcript)

    for dice_roll in dice_rolls:

        # Get window of time before the current roll.
        window_beginning = dice_roll.timestamp - window
        window_middle = dice_roll.timestamp
        window_end = dice_roll.timestamp + window

        # Iterate through captions.
        pre_roll_captions: List[Caption] = []
        post_roll_captions: List[Caption] = []
        for caption in transcript_iterator:
            # Discard captions that occur between the start of the current window, and the end of
            # the last window.
            if caption.start < window_beginning:
                continue
            # Once we've reached a caption outside the current window, stop iterating.
            elif caption.start > window_end:
                break
            # Otherwise determine whether the caption occurred before or after the roll.
            elif caption.end < window_middle:
                pre_roll_captions.append(caption)
            else:
                post_roll_captions.append(caption)

        # Combine the information into an annotation.
        yield {
            'context': ' '.join(caption.text for caption in pre_roll_captions),
            'consequence': ' '.join(caption.text for caption in post_roll_captions),
            'roll_type': dice_roll.roll_type,
            'value': dice_roll.value,
            'critical': dice_roll.critical
        }


def main():
    offset = Timestamp.from_string(args.offset)
    transcript = read_transcript(args.transcript)
    dice_rolls = read_dice_rolls(args.dice_rolls, offset)
    for annotation in generate_annotations(transcript, dice_rolls, window=args.window):
        print(json.dumps(annotation))


if __name__ == '__main__':
    # pylint: disable=invalid-name
    parser = argparse.ArgumentParser('align_transcripts.py',
                                     description='Align dice rolls to transcripts.')
    parser.add_argument('dice_rolls', type=str,
                        help='File containing the dice rolls.')
    parser.add_argument('transcript', type=str,
                        help='File containing the transcript.')
    parser.add_argument('--window', '-w', type=int, default=10,
                        help='Number of seconds worth of dialogue before dice roll to use.')
    parser.add_argument('--offset', '-o', type=str, default="00:00:00",
                        help='Amount to offset timestamps in dice roll files.')
    args, _ = parser.parse_known_args()

    main()
