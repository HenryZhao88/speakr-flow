#import <AVFAudio/AVFAudio.h>
#import <AVFoundation/AVCaptureDevice.h>
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>

// Small C boundary keeps real-time AudioBufferList pointers out of PyObjC.
typedef void (*Receive)(const float *, unsigned);
typedef struct {
    uint64_t callbacks, frames, rejected, gaps, slow_callbacks;
    uint64_t callback_ns, max_callback_ns, first_ns, last_ns;
    double expected_sample;
    bool have_sample;
} CaptureStats;
typedef struct {
    void *engine;
    atomic_bool running;
    double rate;
    CaptureStats stats;
} Capture;

void *sf_voice_create(Receive receive, char *error, unsigned capacity) {
    @autoreleasepool {
        Capture *capture = NULL;
        @try {
        AVAudioEngine *engine = [[AVAudioEngine alloc] init];
        AVAudioInputNode *input = engine.inputNode;
        NSError *failure = nil;
        if (![input setVoiceProcessingEnabled:YES error:&failure]) {
            snprintf(error, capacity, "%s", failure.localizedDescription.UTF8String);
            return NULL;
        }
        input.voiceProcessingBypassed = NO;
        if (@available(macOS 14.0, *)) {
            AVAudioVoiceProcessingOtherAudioDuckingConfiguration ducking = {
                NO, AVAudioVoiceProcessingOtherAudioDuckingLevelMin
            };
            input.voiceProcessingOtherAudioDuckingConfiguration = ducking;
        }
        double rate = [input outputFormatForBus:0].sampleRate;
        if (rate <= 0) {
            snprintf(error, capacity, "Microphone has no input format");
            return NULL;
        }
        capture = calloc(1, sizeof(Capture));
        if (!capture) return NULL;
        atomic_init(&capture->running, false);
        capture->rate = rate;
        AVAudioSinkNode *sink = [[AVAudioSinkNode alloc] initWithReceiverBlock:
            ^OSStatus(const AudioTimeStamp *time, AVAudioFrameCount frames,
                      const AudioBufferList *buffers) {
                CaptureStats *stats = &capture->stats;
                uint64_t began = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
                stats->callbacks++;
                if (!stats->first_ns) stats->first_ns = began;
                stats->last_ns = began;
                if (time->mFlags & kAudioTimeStampSampleTimeValid) {
                    if (stats->have_sample && time->mSampleTime > stats->expected_sample)
                        stats->gaps += time->mSampleTime - stats->expected_sample;
                    stats->expected_sample = time->mSampleTime + frames;
                    stats->have_sample = true;
                }
                if (atomic_load(&capture->running) && buffers->mNumberBuffers &&
                    buffers->mBuffers[0].mNumberChannels == 1 &&
                    buffers->mBuffers[0].mDataByteSize >= frames * sizeof(float)) {
                    stats->frames += frames;
                    receive(buffers->mBuffers[0].mData, frames);
                } else {
                    stats->rejected += frames;
                }
                uint64_t elapsed = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - began;
                stats->callback_ns += elapsed;
                if (elapsed > stats->max_callback_ns) stats->max_callback_ns = elapsed;
                if (elapsed > frames / capture->rate * 1e9) stats->slow_callbacks++;
                return noErr;
            }];
        [engine attachNode:sink];
        AVAudioFormat *format = [[AVAudioFormat alloc]
            initStandardFormatWithSampleRate:rate channels:1];
        [engine connect:input to:sink format:format];
        capture->engine = (__bridge_retained void *)engine;
        return capture;
        } @catch (NSException *exception) {
            free(capture);
            snprintf(error, capacity, "%s", exception.reason.UTF8String);
            return NULL;
        }
    }
}

double sf_voice_rate(Capture *capture) { return capture->rate; }

// Both properties are read-only. Only macOS's user interface can select a mode.
long sf_voice_preferred_mode(void) { return AVCaptureDevice.preferredMicrophoneMode; }
long sf_voice_active_mode(void) { return AVCaptureDevice.activeMicrophoneMode; }
void sf_voice_show_modes(void) {
    [AVCaptureDevice showSystemUserInterface:AVCaptureSystemUserInterfaceMicrophoneModes];
}

bool sf_voice_start(Capture *capture, char *error, unsigned capacity) {
    @autoreleasepool {
        AVAudioEngine *engine = (__bridge AVAudioEngine *)capture->engine;
        NSError *failure = nil;
        memset(&capture->stats, 0, sizeof(capture->stats));
        atomic_store(&capture->running, true);
        @try {
        if (![engine startAndReturnError:&failure]) {
            atomic_store(&capture->running, false);
            snprintf(error, capacity, "%s", failure.localizedDescription.UTF8String);
            return false;
        }
        return true;
        } @catch (NSException *exception) {
            atomic_store(&capture->running, false);
            snprintf(error, capacity, "%s", exception.reason.UTF8String);
            return false;
        }
    }
}

void sf_voice_stop(Capture *capture) {
    @autoreleasepool {
        atomic_store(&capture->running, false);
        [(__bridge AVAudioEngine *)capture->engine stop];
    }
}

// Read only after stop has quiesced the audio callback.
void sf_voice_stats(Capture *capture, CaptureStats *stats) { *stats = capture->stats; }

void sf_voice_destroy(Capture *capture) {
    if (!capture) return;
    sf_voice_stop(capture);
    @autoreleasepool {
        AVAudioEngine *engine = (__bridge_transfer AVAudioEngine *)capture->engine;
        [engine disconnectNodeOutput:engine.inputNode];
        engine = nil;
    }
    free(capture);
}
