# **ngnt-geiger-counter**
This project uses a RadiationD v1.1 cajoe and gives it additional functionality and a simple casing that holds everything together.  
![preview of an assembled device](https://user-images.githubusercontent.com/100175489/219118323-df211fda-93e7-4437-bd8e-3e14d5e2e7f8.jpg)


#### **Things needed:**  
1x assembled RadiationD v1.1 Cajoe (for example look for "diy geiger" on the bay)  
1x LCD2004 Display with i2c (I recommend to buy a display that already has the i2c interface soldered onto)  
1x Wemos D1 R2  
3d printed front- & back-plate https://www.printables.com/model/399474-diy-geiger-counter  
7x M-F dupont cables  

screws:  
8x M3x8mm  
4x M3x12mm  
4x M3 spacer (at least 35mm or more if you don't want to bend some of the dupont cables)  
4x M3 screws fitting the spacer  
12x M3 nuts  

#### **HowTo**  
- print the front and backplate  
- mount the Wemos to the backplate (4x M3x8mm & nuts)  
- mount the LCD to the front plate (4x M3x12mm & nuts)  
- remove the 4 screws on the RadiationD that hold the acryl, mount everything to the front plate (4x M3x8mm)  

- Connect Dupont-cables:  

###### **LCD -> Wemos D1 R2**  
GND -> GND  
VCC -> 3.3V  
SDA -> SDA/D2  
SCL -> SCL/D1  


###### **RadiationD Cajoe V1.1 -> Wemos D1 R2**  
GND -> GND  
5V  -> 5V  
VIN -> D6(GPIO12)  


- use M3 spacer, screws and nuts to mount the front plate on the back plate  
- Flash Wemos D1 R2  



#### **ToBeDone**  
- create a new frontplate with fitting for two switches, one that controls the backlight of the lcd and one that can switch off the speaker (I recommend if you assemble the RadiationD to leave out the speaker for now)
- create network capability & save data; open to suggestions but my current plan is to create an mqtt broker that saves the cpm in a database (all dockerized)
