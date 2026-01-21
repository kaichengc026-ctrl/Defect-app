import numpy as np
import threading
from collections import deque

# encoder length

# sliding window

# ROI crop

class StitchThread(threading.Thread):
    def __init__(self, line_queue, image_queue, height):
        super().__init__(daemon=True)
        self.line_queue = line_queue
        self.image_queue = image_queue
        self.height = height
        self.buffer = deque(maxlen=height)
        self.running = True

    def run(self):
        while self.running:
            line = self.line_queue.get()
            self.buffer.append(line)

            if len(self.buffer) == self.height:
                img = np.stack(self.buffer, axis=0)
                self.buffer.clear()

                try:
                    self.image_queue.put_nowait(img)
                except:
                    pass

    def stop(self):
        self.running = False
