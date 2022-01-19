# Imogen: Imoge Generator

Imogen is a Signal Bot that generates Images using VQGan and CLIP. To have Imogen Generate an Imoge for you you can join the group chat or message Imogen directly.

[Imogen Public](https://signal.group/#CjQKIBMsSPcIQYNjlSA1C1NqvapdjiZX31bdrCpH4ZI9BbwEEhAHOP7DVF1GjizAzYmOnDcY)

[DM Imogen](https://signal.me/#p/+2406171657)

## How To Use

In a Group or a DM message:

```
/imagine [prompt]
```

And Imogen will generate an Image for you based on the prompt.

<img src="examples/imagine.png">

```
/paint [prompt]
```

Will generate an image using the wikiart database, which produces more painty images. 


There are some special commands to specify style for the image.
- /dark_fantasy
- /pastel,
- /psychic,
- /synthwave,
- /ukiyo,
- /vibrant

These are equivalent to appending the style after the imagine prompt. `/dark_fantasy Link` -> `/imagine dark fantasy Link`

Imogen can probably draw in many more styles than those, try out asking for a style, or ask her to channel a particular artist.

## Priority Queue ##

Imogen features a priority queue that will guarantee your image is generated sooner in times of high activity. To make use of Imogen's paid features you must send her a payment with Signal Pay. To do this first make sure you have Payments enabled on Signal. Then DM Imogen and attach a payment using the + button.

![](examples/payment.jpg) ![](examples/payment2.jpg)

 

## Available Commands ##

`/balance`  
returns your Imogen balance for priority requests

`/canceltip `  
cancels registering the next payment as a tip

`/credit `  
marks next transaction as adding to the user's balance (default behaviour for payments)

`/fast `  
Experimental feature

`/help`  
Display the Help text

`/help style`  
lists available styles you can use with /imagine.

`/help {commmand}`  
Explain Specific Command

`/imagine {prompt} `  
Generates an image based on your prompt. Request is handled in the free queue, every free request is addressed and generated sequentially.

`/paint {prompt}`  
Generate an image using the WikiArt database and your prompt, generates painting-like images. Requests handled on the Free queue.

`/priority {prompt} `  
Like /imagine but places your request on a priority queue. Priority items get dedicated workers when available and bypass the free queue.

`/priority-paint {prompt} `  
Like /paint but places your request on the priority queue. Priority items get dedicated workers when available and bypass the free queue. 

`/quick`  
Experimental feature

/tip 
Mark Next Transaction as Tip. Only usable in DMs. /canceltip cancels registering the next payment as a tip.

/status 
Displays Imogen's status

## Advaced Techniques ##


Starting Image:
Imogen can generate your image based on a starting image. Attach an image and use one of the generative commands "/imagine /paint /priority /priority-paint" and Imogen will generate an image based on your prompt and the starting image.

Videos:
You can give imogen multiple prompts separated by "//" and Imogen will generate a video that transitions between the prompts. Will take longer than a regular /imagine.
Example:
/imagine Jane // Cake 

Distort:
You can combine the starting image and the video functionality to make images that dissolve into imoges. 
Attach an image and use one of the generative commands starting with Slash.
with image attached /imagine // Cake


