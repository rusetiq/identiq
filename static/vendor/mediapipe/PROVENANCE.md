# MediaPipe Face Landmarker

Locally served assets from Google's `@mediapipe/tasks-vision` npm package, version **0.10.35**, and the official float16 Face Landmarker model, version **1**. No runtime connection to a CDN or Google is needed.

- Package: https://www.npmjs.com/package/@mediapipe/tasks-vision/v/0.10.35
- Model: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
- Model SHA-256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`
- Source/license: https://github.com/google-ai-edge/mediapipe (Apache 2.0)
- Documentation: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/web_js

The dense mesh is estimated from RGB images. It is not physical depth measurement, proof of liveness, or Apple's Face ID data. The app uses it for live visual guidance; identity matching still uses the existing SFace model.
